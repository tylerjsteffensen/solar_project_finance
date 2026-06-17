"""
CAISO OASIS connectivity / parameter diagnostic.

The OASIS query parameters (report name, version, market, node id) are fiddly
and occasionally change. When a query returns ``ERR_CODE 1000 "No data returned
for the specified selection"`` it almost always means the *parameters* are off,
not the network (you reached OASIS to get that error).

This script sends a minimal **one-day** request for a small matrix of candidate
parameter sets and reports, for each, whether OASIS returned real data or an
error (decoding the OASIS XML error code/description). Run it, then copy the
working combination into ``config.py``.

Usage (from the repo root):

    python -m bess_dispatch.data.diagnose
    python -m bess_dispatch.data.diagnose --date 20230615

It prints a table like:

    queryname        market version node                 -> result
    PRC_LMP          DAM    12      TH_SP15_GEN-APND      -> OK: 24 rows, mean $...
    PRC_LMP          DAM    1       TH_SP15_GEN-APND      -> ERR 1000: No data ...
    ...
"""

from __future__ import annotations

import argparse
import io
import re
import time
import zipfile

import pandas as pd
import requests

from .. import config
from .caiso_fetch import oasis_url, rows_from_oasis_csv

# CAISO's Acceptable Use Policy requires a gap between requests (HTTP 429
# otherwise). Space probes out by at least this many seconds.
INTER_REQUEST_DELAY = 7


def _window(date: str, hour: int = 8, days: int = 1) -> tuple[str, str]:
    """Build (startdatetime, enddatetime) GMT strings for a date window.

    Args:
        date: Start date YYYYMMDD.
        hour: GMT hour offset for the day boundary (8 = midnight PST, 7 = PDT).
        days: Window length in days.

    Returns:
        Tuple of OASIS-formatted start/end datetime strings.
    """
    start_dt = pd.to_datetime(date)
    end_dt = start_dt + pd.Timedelta(days=days)
    return (f"{start_dt:%Y%m%d}T{hour:02d}:00-0000",
            f"{end_dt:%Y%m%d}T{hour:02d}:00-0000")


def _build_probes(date: str) -> list[tuple[str, dict]]:
    """Construct the labelled probe matrix for a given date.

    The matrix is designed to triangulate the failure: it holds the
    known-populated DLAP_SCE-APND node and varies version, the day-boundary
    offset (08 vs 07 GMT), and the window length; it includes a
    ``grp_type=ALL_APNODES`` probe (no node at all) which, if it returns data,
    proves the report/date/format/version are correct and isolates the problem
    to the node id.

    Args:
        date: Trade date YYYYMMDD.

    Returns:
        List of (label, params) pairs.
    """
    recent = (pd.Timestamp.today().normalize() - pd.Timedelta(days=10)).strftime("%Y%m%d")
    base = {"queryname": "PRC_LMP", "market_run_id": "DAM",
            "resultformat": config.CAISO_RESULTFORMAT}

    def p(**kw):
        d = dict(base)
        d.update(kw)
        return d

    s08, e08 = _window(date, hour=8)
    s07, e07 = _window(date, hour=7)
    s2, e2 = _window(date, hour=8, days=2)
    sr, er = _window(recent, hour=8)

    return [
        ("DLAP v1  T08 1d",
         p(version=1, node="DLAP_SCE-APND", startdatetime=s08, enddatetime=e08)),
        ("DLAP v1  T07 1d",
         p(version=1, node="DLAP_SCE-APND", startdatetime=s07, enddatetime=e07)),
        ("DLAP v1  T08 2d",
         p(version=1, node="DLAP_SCE-APND", startdatetime=s2, enddatetime=e2)),
        ("DLAP v1  recent",
         p(version=1, node="DLAP_SCE-APND", startdatetime=sr, enddatetime=er)),
        ("ALLNODES v1 T08",  # no node -> proves report/date/format if it works
         p(version=1, grp_type="ALL_APNODES", startdatetime=s08, enddatetime=e08)),
        ("SP15hub v1 T08",
         p(version=1, node="TH_SP15_GEN-APND", startdatetime=s08, enddatetime=e08)),
    ]


def _probe(params: dict) -> tuple[str, str]:
    """Send one OASIS request and summarize the outcome.

    Args:
        params: Full query parameters for the SingleZip endpoint.

    Returns:
        Tuple of (request_url, human-readable result string).
    """
    url = oasis_url(params)  # preserve ':' in datetimes (see oasis_url docstring)
    resp = None
    for attempt in range(2):  # one retry if rate-limited (HTTP 429)
        try:
            resp = requests.get(
                url,
                timeout=config.CAISO_REQUEST_TIMEOUT,
                headers={"User-Agent": "bess-dispatch-research/1.0"},
            )
        except Exception as exc:  # noqa: BLE001
            return url, f"REQUEST FAILED: {type(exc).__name__}: {exc}"
        if resp.status_code == 429 and attempt == 0:
            time.sleep(INTER_REQUEST_DELAY)
            continue
        break

    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code == 200 and "zip" in ctype.lower():
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                name = zf.namelist()[0]
                body = zf.read(name)
            if body.lstrip().startswith(b"<"):  # XML error inside the zip
                return url, _decode_xml_error(body.decode("utf-8", "ignore"))
            rows = rows_from_oasis_csv(pd.read_csv(io.BytesIO(body)))
            if rows.empty:
                return url, "OK transport but 0 LMP rows parsed (check LMP_TYPE)"
            return url, f"OK: {len(rows)} rows, mean ${rows['lmp'].mean():.2f}/MWh"
        except Exception as exc:  # noqa: BLE001
            return url, f"unzip/parse error: {exc}"

    text = resp.content.decode("utf-8", "ignore")
    return url, f"HTTP {resp.status_code} {ctype}: {_decode_xml_error(text)}"


def _decode_xml_error(text: str) -> str:
    """Extract ERR_CODE / ERR_DESC from an OASIS XML error payload.

    Args:
        text: Decoded response body.

    Returns:
        ``ERR <code>: <desc>`` if found, else a trimmed snippet of the body.
    """
    code = re.search(r"ERR_CODE>([^<]+)<", text)
    desc = re.search(r"ERR_DESC>([^<]+)<", text)
    if code or desc:
        return f"ERR {code.group(1) if code else '?'}: {desc.group(1) if desc else '?'}"
    return text.strip().replace("\n", " ")[:120]


def run(date: str = "20230117") -> None:
    """Probe the triangulation matrix for a date and print results + URLs.

    Args:
        date: Trade date to test, as YYYYMMDD.
    """
    probes = _build_probes(date)
    print(f"Probing CAISO OASIS (base test date {date}). Each probe prints its "
          f"exact request URL so you can also paste it into a browser:\n")
    for i, (label, params) in enumerate(probes):
        if i:  # space requests out to respect CAISO's Acceptable Use Policy
            time.sleep(INTER_REQUEST_DELAY)
        url, result = _probe(params)
        print(f"[{label}] -> {result}")
        print(f"    {url}\n", flush=True)
    print(
        "Read it like this:\n"
        "  * If 'ALLNODES' returns OK but the node probes do not -> the node\n"
        "    IDs are the problem (report/date/format/version are all correct).\n"
        "  * If a 'DLAP' probe returns OK -> copy that probe's exact params\n"
        "    (version, offset, window) into config.py.\n"
        "  * If everything still fails -> paste one printed URL into a browser\n"
        "    and share the raw response so we can see what OASIS objects to."
    )


def main() -> None:
    """CLI entry point for the diagnostic."""
    parser = argparse.ArgumentParser(description="Diagnose CAISO OASIS LMP queries.")
    parser.add_argument("--date", default="20230117",
                        help="Trade date YYYYMMDD to test (default: %(default)s)")
    args = parser.parse_args()
    run(args.date)


if __name__ == "__main__":
    main()
