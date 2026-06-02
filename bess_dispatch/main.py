"""
Entry point: run the full BESS dispatch analysis pipeline.

Fetches (or loads cached / synthetic) CAISO SP15 LMP data, generates synthetic
solar, runs the greedy hourly dispatch, computes metrics, and prints a console
report comparing results against the Excel project finance baseline.

Usage:
    python -m bess_dispatch.main
    python -m bess_dispatch.main --year 2023 --refresh

The interactive dashboard (``streamlit run bess_dispatch/dashboard/app.py``)
calls the same building blocks via :func:`run_pipeline`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd

from . import config
from .analysis import comparison, metrics
from .data.caiso_fetch import get_hourly_lmp
from .data.solar_gen import generate_solar
from .dispatch.optimizer import run_dispatch


@dataclass
class PipelineResult:
    """Container for all artifacts produced by a pipeline run.

    Attributes:
        lmp: Hourly LMP DataFrame (``.attrs['source']`` is 'caiso'/'synthetic').
        solar: Hourly synthetic solar DataFrame.
        dispatch: Hourly dispatch DataFrame.
        metrics: Headline metrics dict.
        comparison: Dispatch-vs-Excel comparison table.
    """

    lmp: pd.DataFrame
    solar: pd.DataFrame
    dispatch: pd.DataFrame
    metrics: dict
    comparison: pd.DataFrame


def run_pipeline(
    year: int = config.DATA_YEAR,
    *,
    force_refresh: bool = False,
) -> PipelineResult:
    """Run the end-to-end analysis pipeline.

    Args:
        year: Calendar year of LMP data to analyze.
        force_refresh: Bypass caches and re-fetch LMP data from OASIS.

    Returns:
        A populated :class:`PipelineResult`.
    """
    lmp = get_hourly_lmp(year, force_refresh=force_refresh)
    solar = generate_solar(year)
    dispatch = run_dispatch(lmp, year=config.PROJECT_YEAR)
    return PipelineResult(
        lmp=lmp,
        solar=solar,
        dispatch=dispatch,
        metrics=metrics.summarize(dispatch),
        comparison=comparison.comparison_table(dispatch),
    )


def print_report(result: PipelineResult) -> None:
    """Print a formatted console report for a pipeline run.

    Args:
        result: The pipeline result to summarize.
    """
    src = result.lmp.attrs.get("source", "unknown")
    print("=" * 72)
    print("  BESS HOURLY DISPATCH — CAISO SP15  (Year 1, nominal USD)")
    print("=" * 72)
    print(f"LMP data source        : {src}"
          + ("  [SYNTHETIC FALLBACK — not real market data]" if src == "synthetic" else ""))
    print(f"Hours analyzed         : {len(result.lmp):,}")
    print(f"Mean SP15 LMP          : ${result.lmp['lmp'].mean():,.2f}/MWh")
    print(f"Solar realized CF      : "
          f"{result.solar['solar_mw'].sum() / (config.SOLAR_MWAC * len(result.solar)):.1%}")
    print("-" * 72)
    print("DISPATCH RESULTS")
    m = result.metrics
    print(f"  Annual arbitrage rev : ${m['annual_revenue']:,.0f}")
    print(f"  Effective spread     : ${m['effective_spread']:.2f}/MWh")
    print(f"  Cycles per day       : {m['cycles_per_day']:.2f}")
    print(f"  RA binding (peak hrs): {m['ra_binding_pct']:.1f}%")
    print(f"  BESS capacity factor : {m['bess_capacity_factor']:.1%}")
    print("-" * 72)
    print("COMPARISON vs EXCEL BASELINE")
    print(result.comparison.to_string(index=False,
          float_format=lambda x: f"{x:,.2f}"))
    print("-" * 72)
    print("TOTAL BESS REVENUE BRIDGE")
    arb = m["annual_revenue"]
    total = arb + config.EXCEL_RA_REVENUE + config.EXCEL_ANCILLARY_REVENUE
    print(f"  Arbitrage (dispatch) : ${arb:,.0f}")
    print(f"  Resource Adequacy    : ${config.EXCEL_RA_REVENUE:,.0f}")
    print(f"  Ancillary Services   : ${config.EXCEL_ANCILLARY_REVENUE:,.0f}")
    print(f"  TOTAL BESS revenue   : ${total:,.0f}  "
          f"(Excel: ${config.EXCEL_TOTAL_BESS_REVENUE:,.0f})")
    print("=" * 72)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run the BESS dispatch pipeline.")
    parser.add_argument("--year", type=int, default=config.DATA_YEAR,
                        help="Calendar year of LMP data (default: %(default)s)")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-fetch from OASIS, ignoring caches")
    args = parser.parse_args()

    result = run_pipeline(args.year, force_refresh=args.refresh)
    print_report(result)


if __name__ == "__main__":
    main()
