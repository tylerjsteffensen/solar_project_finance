"""
CAISO OASIS API client for SP15 LMP data.

Pulls PRC_LMP day-ahead-market (DAM) prices for the SP15 generation trading hub
(``TH_SP15_GEN-APND``), in 31-day chunks (the OASIS per-request limit), with
exponential-backoff retries and on-disk caching so repeat runs never re-hit the
API. The report/market/node are all configurable in ``config.py`` (e.g. to
switch to 5-minute real-time via PRC_INTVL_LMP / RTM).

DAM LMP is natively hourly; this module still routes it through a resample +
full-year reindex so any gaps are filled and the output is a clean,
local-clock hourly series for the requested year (the same path also handles
sub-hourly inputs if a real-time report is configured).

Graceful degradation
---------------------
OASIS is aggressively rate-limited and frequently returns HTTP 403 to
unauthenticated / high-frequency callers, and may be blocked entirely behind
corporate or sandbox firewalls. When a fetch cannot complete and
``config.ALLOW_SYNTHETIC_LMP_FALLBACK`` is True, the client returns a
deterministic synthetic SP15 price series with a realistic duck-curve shape so
the dispatch model and dashboard remain fully runnable offline. The returned
DataFrame carries a ``source`` attribute ("caiso" or "synthetic") and the
hourly frame includes an ``is_synthetic`` column for transparency.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from .. import config


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_hourly_lmp(
    year: int = config.DATA_YEAR,
    *,
    force_refresh: bool = False,
    allow_fallback: bool | None = None,
) -> pd.DataFrame:
    """Return one calendar year of hourly SP15 LMP, fetching+caching as needed.

    Loads the consolidated hourly cache if present; otherwise fetches the year
    in 31-day chunks from OASIS, caches each chunk, resamples to hourly, and
    writes a consolidated hourly cache. Falls back to synthetic data if the API
    is unreachable and fallback is allowed.

    Args:
        year: Calendar year to retrieve.
        force_refresh: If True, ignore caches and re-fetch from the API.
        allow_fallback: Override ``config.ALLOW_SYNTHETIC_LMP_FALLBACK``.

    Returns:
        DataFrame indexed by hourly ``timestamp`` (tz-naive, local clock) with
        columns ``lmp`` ($/MWh) and ``is_synthetic`` (bool). The DataFrame has a
        ``.attrs['source']`` of "caiso" or "synthetic".
    """
    if allow_fallback is None:
        allow_fallback = config.ALLOW_SYNTHETIC_LMP_FALLBACK

    hourly_cache = os.path.join(config.CACHE_DIR, f"sp15_lmp_hourly_{year}.csv")
    if not force_refresh and os.path.exists(hourly_cache):
        df = pd.read_csv(hourly_cache, index_col="timestamp", parse_dates=["timestamp"])
        df.attrs["source"] = "synthetic" if bool(df["is_synthetic"].iloc[0]) else "caiso"
        return df

    try:
        raw = _fetch_year_from_oasis(year, force_refresh=force_refresh)
        hourly = _resample_to_hourly(raw, year)
        hourly["is_synthetic"] = False
        hourly.attrs["source"] = "caiso"
    except Exception as exc:  # noqa: BLE001 - any failure -> fallback or raise
        if not allow_fallback:
            raise
        print(
            f"[caiso_fetch] OASIS fetch failed ({type(exc).__name__}: {exc}). "
            f"Falling back to synthetic SP15 prices for {year}."
        )
        hourly = generate_synthetic_lmp(year)
        hourly["is_synthetic"] = True
        hourly.attrs["source"] = "synthetic"

    hourly.to_csv(hourly_cache)
    return hourly


# --------------------------------------------------------------------------- #
# OASIS fetching
# --------------------------------------------------------------------------- #
def _chunk_ranges(year: int, chunk_days: int = config.CAISO_CHUNK_DAYS):
    """Yield (start, end) datetimes spanning the year in <=chunk_days windows."""
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        yield cur, nxt
        cur = nxt


def _fetch_year_from_oasis(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """Fetch and concatenate all 31-day chunks for a year from OASIS.

    Each chunk is cached as parquet under the cache dir. Raises on unrecoverable
    failure of any chunk (caller decides whether to fall back).
    """
    frames = []
    for i, (start, end) in enumerate(_chunk_ranges(year)):
        # Space live requests out to respect CAISO's Acceptable Use Policy
        # (rapid back-to-back calls return HTTP 429). Cache hits skip the wait.
        if i and not _chunk_is_cached(start, end):
            time.sleep(config.CAISO_INTER_REQUEST_DELAY)
        frames.append(_fetch_chunk(start, end, force_refresh=force_refresh))
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset="interval_start").sort_values("interval_start")
    return raw


def _chunk_is_cached(start: datetime, end: datetime) -> bool:
    """Whether a chunk's CSV cache already exists (so no live request is needed)."""
    tag = f"{start:%Y%m%d}_{end:%Y%m%d}"
    return os.path.exists(os.path.join(config.CACHE_DIR, f"sp15_lmp_raw_{tag}.csv"))


def _fetch_chunk(
    start: datetime, end: datetime, *, force_refresh: bool = False
) -> pd.DataFrame:
    """Fetch a single 31-day chunk of 5-minute LMP, with cache + retries.

    Args:
        start: Inclusive chunk start (local clock).
        end: Exclusive chunk end (local clock).
        force_refresh: Ignore any cached chunk.

    Returns:
        DataFrame with columns ``interval_start`` (datetime) and ``lmp`` (float).
    """
    tag = f"{start:%Y%m%d}_{end:%Y%m%d}"
    chunk_cache = os.path.join(config.CACHE_DIR, f"sp15_lmp_raw_{tag}.csv")
    if not force_refresh and os.path.exists(chunk_cache):
        return pd.read_csv(chunk_cache, parse_dates=["interval_start"])

    # OASIS expects GMT timestamps; SP15 local is UTC-8 (PST). Use a fixed -0800
    # offset on the request; we re-localize to local clock during resampling.
    params = {
        "queryname": config.CAISO_QUERYNAME,
        "market_run_id": config.CAISO_MARKET_RUN_ID,
        "node": config.CAISO_NODE,
        "version": config.CAISO_VERSION,
        "resultformat": config.CAISO_RESULTFORMAT,
        "startdatetime": f"{start:%Y%m%d}T08:00-0000",
        "enddatetime": f"{end:%Y%m%d}T08:00-0000",
    }

    content = _request_with_retries(params, tag)
    df = _parse_oasis_zip(content)
    df.to_csv(chunk_cache, index=False)
    return df


def _request_with_retries(params: dict, tag: str) -> bytes:
    """GET an OASIS SingleZip payload, retrying with exponential backoff.

    Args:
        params: Query parameters for the SingleZip endpoint.
        tag: Human-readable chunk label for log messages.

    Returns:
        Raw response content (a zip archive of one CSV).

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    last_err: Exception | None = None
    for attempt in range(config.CAISO_MAX_RETRIES):
        try:
            resp = requests.get(
                config.CAISO_BASE_URL,
                params=params,
                timeout=config.CAISO_REQUEST_TIMEOUT,
                headers={"User-Agent": "bess-dispatch-research/1.0"},
            )
            ctype = resp.headers.get("Content-Type", "")
            # OASIS signals throttling/errors with a tiny text/plain body or 4xx.
            if resp.status_code == 200 and "zip" in ctype.lower():
                return resp.content
            raise RuntimeError(
                f"HTTP {resp.status_code} ctype={ctype!r} body={resp.content[:120]!r}"
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            wait = config.CAISO_BACKOFF_BASE_SEC * (2 ** attempt)
            print(
                f"[caiso_fetch] chunk {tag} attempt {attempt + 1}/"
                f"{config.CAISO_MAX_RETRIES} failed: {exc}. Retrying in {wait}s."
            )
            time.sleep(wait)
    raise RuntimeError(f"OASIS fetch failed for chunk {tag}: {last_err}")


def _parse_oasis_zip(content: bytes) -> pd.DataFrame:
    """Parse a zipped OASIS PRC_LMP CSV into 5-minute LMP rows.

    Keeps only the total LMP item (LMP_PRC) and returns the interval-start time
    and price.

    Args:
        content: Raw zip bytes from the SingleZip endpoint.

    Returns:
        DataFrame with ``interval_start`` (datetime) and ``lmp`` (float).
    """
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = zf.namelist()[0]
        csv_bytes = zf.read(name)
    df = pd.read_csv(io.BytesIO(csv_bytes))
    return rows_from_oasis_csv(df)


def rows_from_oasis_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw OASIS PRC_LMP CSV (already read) to interval/price rows.

    Resolves the (slightly variable) OASIS column names, keeps only the total
    LMP item, and returns tidy rows. Shared by the live fetcher and the manual
    ingest path so both interpret OASIS files identically.

    Args:
        df: A DataFrame read directly from an OASIS PRC_LMP CSV.

    Returns:
        DataFrame with ``interval_start`` (tz-aware UTC) and ``lmp`` (float).
    """
    item_col = _first_present(df, ["LMP_TYPE", "XML_DATA_ITEM", "DATA_ITEM"])
    if item_col is not None:
        df = df[df[item_col].astype(str).str.contains("LMP_PRC|LMP$", regex=True)]
    start_col = _first_present(
        df, ["INTERVALSTARTTIME_GMT", "INTERVAL_START_GMT", "OPR_DT"]
    )
    value_col = _first_present(df, ["MW", "VALUE", "PRC"])
    if start_col is None or value_col is None:
        raise ValueError(
            "Could not find OASIS time/value columns in CSV. "
            f"Columns present: {list(df.columns)}"
        )
    out = pd.DataFrame(
        {
            "interval_start": pd.to_datetime(df[start_col], utc=True, errors="coerce"),
            "lmp": pd.to_numeric(df[value_col], errors="coerce"),
        }
    ).dropna()
    return out


def _first_present(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first candidate column name that exists in ``df``."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _resample_to_hourly(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    """Convert 5-minute GMT LMP rows to a clean local-clock hourly series.

    Converts GMT -> US/Pacific local clock, averages sub-hourly intervals to
    hourly, and reindexes onto a complete 8760/8784-hour grid, forward/back
    filling any gaps so downstream code never sees missing hours.

    Args:
        raw: 5-minute rows with tz-aware ``interval_start`` (UTC) and ``lmp``.
        year: Calendar year being processed.

    Returns:
        DataFrame indexed by tz-naive hourly ``timestamp`` with column ``lmp``.
    """
    s = raw.set_index("interval_start")["lmp"].sort_index()
    try:
        # Preferred: true US/Pacific local clock (handles PST/PDT correctly).
        s = s.tz_convert("US/Pacific").tz_localize(None)
    except Exception:  # noqa: BLE001 - missing IANA tz database (e.g. Windows w/o tzdata)
        # Fallback: fixed PST (UTC-8) offset. Avoids a hard dependency on the
        # tz database; DST is ignored, which can shift summer hours by 1h.
        print("[caiso_fetch] tz database unavailable; using fixed UTC-8 (PST). "
              "Install 'tzdata' for exact DST handling.")
        s.index = (s.index + pd.Timedelta(hours=-8)).tz_localize(None)
    hourly = s.resample("1h").mean()

    full_index = pd.date_range(
        f"{year}-01-01 00:00", f"{year}-12-31 23:00", freq="1h"
    )
    hourly = hourly.reindex(full_index)
    n_missing = int(hourly.isna().sum())
    if n_missing:
        print(f"[caiso_fetch] filling {n_missing} missing hourly values via interpolation.")
        hourly = hourly.interpolate(limit_direction="both")

    out = hourly.to_frame("lmp")
    out.index.name = "timestamp"
    return out


# --------------------------------------------------------------------------- #
# Synthetic fallback
# --------------------------------------------------------------------------- #
def generate_synthetic_lmp(year: int = config.DATA_YEAR) -> pd.DataFrame:
    """Generate a deterministic, realistic synthetic SP15 RTM hourly LMP series.

    Reproduces the salient features of CAISO SP15 real-time prices so the
    dispatch model behaves sensibly offline:

    * A duck-curve daily shape: depressed (occasionally negative) midday solar
      hours and a sharp HE17-20 evening peak.
    * Seasonal amplitude (hot-summer evening scarcity > shoulder months).
    * Hour-to-hour noise plus occasional upward price spikes.

    This is NOT a forecast; it is a stand-in keyed to a fixed seed so results
    are reproducible. Swap in real OASIS data (the default path) for analysis.

    Args:
        year: Calendar year to generate (controls leap-year length).

    Returns:
        DataFrame indexed by tz-naive hourly ``timestamp`` with column ``lmp``.
    """
    rng = np.random.default_rng(config.SYNTHETIC_SEED + year)
    index = pd.date_range(f"{year}-01-01 00:00", f"{year}-12-31 23:00", freq="1h")

    hour = index.hour.to_numpy()
    doy = index.dayofyear.to_numpy()

    # Base diurnal shape (normalized multiplier around 1.0): low overnight,
    # deep midday solar trough, tall evening peak.
    diurnal_base = np.array([
        0.95, 0.88, 0.83, 0.82, 0.85, 0.95,   # HE 1-6  (00-05)
        1.05, 1.00, 0.80, 0.60, 0.45, 0.38,   # HE 7-12 (06-11)
        0.35, 0.36, 0.42, 0.60, 0.95, 1.55,   # HE 13-18 (12-17)
        1.90, 1.80, 1.45, 1.20, 1.05, 1.00,   # HE 19-24 (18-23)
    ])
    diurnal = diurnal_base[hour]

    # Seasonal envelope: summer (peak ~ Jul/Aug) lifts evening scarcity.
    seasonal = 1.0 + config.SOLAR_SEASONAL_SWING * np.cos(2 * np.pi * (doy - 205) / 365)

    base_price = 42.0  # $/MWh annual-average target before noise
    price = base_price * diurnal * seasonal

    # Hour-to-hour multiplicative noise.
    price *= rng.normal(1.0, 0.18, size=len(index)).clip(0.4, 2.2)

    # Occasional evening scarcity spikes (~1.5% of hours), concentrated 17-21.
    spike_mask = (rng.random(len(index)) < 0.015) & np.isin(hour, [17, 18, 19, 20, 21])
    price[spike_mask] *= rng.uniform(2.5, 6.0, size=spike_mask.sum())

    # Rare midday oversupply -> small negative prices.
    neg_mask = (rng.random(len(index)) < 0.03) & np.isin(hour, [10, 11, 12, 13, 14])
    price[neg_mask] = rng.uniform(-15.0, -1.0, size=neg_mask.sum())

    out = pd.DataFrame({"lmp": price}, index=index)
    out.index.name = "timestamp"
    return out


if __name__ == "__main__":
    df = get_hourly_lmp()
    print(f"source={df.attrs['source']} rows={len(df)} mean=${df['lmp'].mean():.2f}/MWh")
    print(df.head())
