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
from .caiso_fetch import rows_from_oasis_csv

# CAISO's Acceptable Use Policy requires a gap between requests (HTTP 429
# otherwise). Space probes out by at least this many seconds.
INTER_REQUEST_DELAY = 7

# Candidate parameter sets to probe, most-likely first. Each is
# (queryname, market_run_id, version, node). We vary BOTH the version (1 vs 12)
# and the node, and include DLAP_SCE-APND -- a major, definitely-populated load
# aggregation node -- as a reference: if DLAP works at some version, that
# version is correct and any remaining failures are node-name problems.
CANDIDATES = [
    ("PRC_LMP", "DAM", 1, "TH_SP15_GEN-APND"),    # SP15 hub, older version
    ("PRC_LMP", "DAM", 12, "TH_SP15_GEN-APND"),   # SP15 hub, current version
    ("PRC_LMP", "DAM", 1, "DLAP_SCE-APND"),       # reference node, version 1
    ("PRC_LMP", "DAM", 12, "DLAP_SCE-APND"),      # reference node, version 12
    ("PRC_LMP", "DAM", 1, "SP15_GEN-APND"),       # alt hub spelling (no TH_)
    ("PRC_LMP", "DAM", 12, "SP15_GEN-APND"),
]


def _probe(
    queryname: str, market: str, version: int, node: str, date: str
) -> str:
    """Send one minimal one-day OASIS request and summarize the outcome.

    Args:
        queryname: OASIS report name.
        market: market_run_id (DAM / RTM).
        version: report version number.
        node: APnode id.
        date: Start date as YYYYMMDD (one trade day is requested).

    Returns:
        A human-readable result string (data summary or decoded OASIS error).
    """
    start = f"{date}T08:00-0000"
    end_dt = pd.to_datetime(date) + pd.Timedelta(days=1)
    end = f"{end_dt:%Y%m%d}T08:00-0000"
    params = {
        "queryname": queryname,
        "market_run_id": market,
        "version": version,
        "node": node,
        "resultformat": config.CAISO_RESULTFORMAT,
        "startdatetime": start,
        "enddatetime": end,
    }
    resp = None
    for attempt in range(2):  # one retry if rate-limited (HTTP 429)
        try:
            resp = requests.get(
                config.CAISO_BASE_URL, params=params,
                timeout=config.CAISO_REQUEST_TIMEOUT,
                headers={"User-Agent": "bess-dispatch-research/1.0"},
            )
        except Exception as exc:  # noqa: BLE001
            return f"REQUEST FAILED: {type(exc).__name__}: {exc}"
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
            # An XML error can be returned inside the zip too.
            if body.lstrip().startswith(b"<"):
                return _decode_xml_error(body.decode("utf-8", "ignore"))
            rows = rows_from_oasis_csv(pd.read_csv(io.BytesIO(body)))
            if rows.empty:
                return "OK transport but 0 LMP rows parsed (check LMP_TYPE column)"
            return f"OK: {len(rows)} rows, mean ${rows['lmp'].mean():.2f}/MWh"
        except Exception as exc:  # noqa: BLE001
            return f"unzip/parse error: {exc}"

    # Non-zip response: usually an XML/plain error.
    text = resp.content.decode("utf-8", "ignore")
    return f"HTTP {resp.status_code} {ctype}: {_decode_xml_error(text)}"


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
    """Probe every candidate parameter set for one day and print a report.

    Args:
        date: Trade date to test, as YYYYMMDD.
    """
    print(f"Probing CAISO OASIS for a single day ({date}). "
          f"One row of output per candidate parameter set:\n")
    header = f"{'queryname':<15} {'mkt':<4} {'ver':<4} {'node':<22} -> result"
    print(header)
    print("-" * len(header))
    for i, (qn, mkt, ver, node) in enumerate(CANDIDATES):
        if i:  # space requests out to respect CAISO's Acceptable Use Policy
            time.sleep(INTER_REQUEST_DELAY)
        result = _probe(qn, mkt, ver, node, date)
        print(f"{qn:<15} {mkt:<4} {ver:<4} {node:<22} -> {result}", flush=True)
    print(
        "\nUse the first combination that says 'OK': copy its queryname, "
        "market, version, and node into the CAISO_* settings in config.py."
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
