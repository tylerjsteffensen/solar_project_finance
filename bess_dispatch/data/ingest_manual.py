"""
Ingest manually-downloaded CAISO OASIS files into the dashboard's data cache.

Use this when the automatic OASIS fetch is blocked (CAISO frequently throttles
programmatic callers with HTTP 403). Download the PRC_LMP files yourself from a
browser, drop them in a folder, and run this module to convert them into the
exact hourly cache file the dashboard reads (``sp15_lmp_hourly_<year>.csv``).

It accepts whatever OASIS gives you:
* ``.zip`` archives straight from the SingleZip endpoint, or
* already-extracted ``.csv`` files.

Usage (from the repository root):

    python -m bess_dispatch.data.ingest_manual --year 2023 --src C:\\path\\to\\downloads

If ``--src`` is omitted it defaults to a ``manual_downloads`` folder inside the
cache directory. After a successful run, just launch the dashboard normally and
it will load the real data (the banner will no longer say "synthetic").
"""

from __future__ import annotations

import argparse
import glob
import io
import os
import zipfile

import pandas as pd

from .. import config
from .caiso_fetch import _resample_to_hourly, rows_from_oasis_csv


def _read_one_file(path: str) -> pd.DataFrame:
    """Parse a single downloaded OASIS file (zip or csv) into interval/price rows.

    Args:
        path: Path to a ``.zip`` (containing one CSV) or a ``.csv`` file.

    Returns:
        DataFrame with ``interval_start`` and ``lmp``; empty if unparseable.
    """
    try:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                # An OASIS zip may contain several CSV members; read them all.
                frames = [
                    rows_from_oasis_csv(pd.read_csv(io.BytesIO(zf.read(n))))
                    for n in zf.namelist()
                    if n.lower().endswith(".csv")
                ]
            return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if path.lower().endswith(".csv"):
            return rows_from_oasis_csv(pd.read_csv(path))
    except Exception as exc:  # noqa: BLE001 - skip bad files, keep going
        print(f"[ingest_manual] skipping {os.path.basename(path)}: {exc}")
    return pd.DataFrame()


def ingest_directory(
    year: int = config.DATA_YEAR, src_dir: str | None = None
) -> str:
    """Build the hourly LMP cache from manually-downloaded OASIS files.

    Reads every ``.zip`` and ``.csv`` in ``src_dir``, concatenates and
    de-duplicates the 5-minute rows, resamples to a complete hourly series, and
    writes ``sp15_lmp_hourly_<year>.csv`` into the cache directory.

    Args:
        year: Calendar year the files cover (used for the cache filename and the
            full-year hourly grid).
        src_dir: Folder containing the downloaded files. Defaults to
            ``<cache>/manual_downloads``.

    Returns:
        Path to the written hourly cache CSV.

    Raises:
        FileNotFoundError: If ``src_dir`` does not exist.
        ValueError: If no parseable LMP rows were found.
    """
    if src_dir is None:
        src_dir = os.path.join(config.CACHE_DIR, "manual_downloads")
    if not os.path.isdir(src_dir):
        raise FileNotFoundError(
            f"Source folder not found: {src_dir}\n"
            "Create it and place your downloaded OASIS .zip/.csv files inside."
        )

    files = sorted(
        glob.glob(os.path.join(src_dir, "*.zip"))
        + glob.glob(os.path.join(src_dir, "*.csv"))
    )
    if not files:
        raise ValueError(f"No .zip or .csv files found in {src_dir}")

    print(f"[ingest_manual] found {len(files)} file(s) in {src_dir}")
    frames = [df for f in files if not (df := _read_one_file(f)).empty]
    if not frames:
        raise ValueError(
            "No valid LMP rows parsed. Make sure these are CAISO PRC_LMP "
            "(real-time) downloads for node SP15GEN-APND."
        )

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset="interval_start").sort_values("interval_start")
    print(f"[ingest_manual] parsed {len(raw):,} interval rows; resampling to hourly…")

    hourly = _resample_to_hourly(raw, year)
    hourly["is_synthetic"] = False

    out_path = os.path.join(config.CACHE_DIR, f"sp15_lmp_hourly_{year}.csv")
    hourly.to_csv(out_path)
    print(
        f"[ingest_manual] wrote {len(hourly):,} hourly rows to {out_path}\n"
        f"[ingest_manual] mean LMP = ${hourly['lmp'].mean():.2f}/MWh. "
        f"Launch the dashboard to use it."
    )
    return out_path


def main() -> None:
    """CLI entry point for manual ingest."""
    parser = argparse.ArgumentParser(description="Ingest manual OASIS downloads.")
    parser.add_argument("--year", type=int, default=config.DATA_YEAR,
                        help="Year the files cover (default: %(default)s)")
    parser.add_argument("--src", type=str, default=None,
                        help="Folder of downloaded .zip/.csv files "
                             "(default: <cache>/manual_downloads)")
    args = parser.parse_args()
    ingest_directory(args.year, args.src)


if __name__ == "__main__":
    main()
