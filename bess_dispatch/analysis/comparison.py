"""
Compare the hourly dispatch model against the Excel project finance baseline.

Produces:

* A side-by-side metrics table (dispatch model vs. Excel Year-1 assumptions).
* A waterfall decomposition of the arbitrage-revenue variance into volume
  (cycles) and price (spread) effects.
* Two sensitivity sweeps:
    - Annual arbitrage revenue vs. BESS duration (2/4/6/8h), re-running the
      greedy dispatch at each duration (power held constant).
    - Annual arbitrage revenue vs. cycles/day (1.0/1.3/1.5/1.7) under the
      Excel's linear-cycles methodology, for apples-to-apples comparison.

All values are nominal USD, Year 1.
"""

from __future__ import annotations

import pandas as pd

from .. import config
from ..dispatch.optimizer import run_dispatch
from . import metrics


def comparison_table(dispatch: pd.DataFrame) -> pd.DataFrame:
    """Build the dispatch-vs-Excel headline metrics comparison table.

    Args:
        dispatch: Hourly dispatch DataFrame from ``run_dispatch``.

    Returns:
        DataFrame with columns ``metric``, ``dispatch_model``, ``excel_model``,
        ``delta``, ``delta_pct``.
    """
    m = metrics.summarize(dispatch)
    rows = [
        (
            "Annual arbitrage revenue ($)",
            m["annual_revenue"],
            config.EXCEL_ARBITRAGE_REVENUE,
        ),
        (
            "Effective spread ($/MWh)",
            m["effective_spread"],
            config.EXCEL_EFFECTIVE_SPREAD,
        ),
        ("Cycles per day", m["cycles_per_day"], config.EXCEL_CYCLES_PER_DAY),
        (
            "Annual discharge (MWh)",
            m["total_discharge_mwh"],
            config.EXCEL_NET_DISCHARGE_MWH * config.EXCEL_ARBITRAGE_ALLOCATION,
        ),
        ("BESS capacity factor (%)", m["bess_capacity_factor"] * 100.0, float("nan")),
        ("RA constraint binding (% peak hrs)", m["ra_binding_pct"], float("nan")),
    ]
    df = pd.DataFrame(rows, columns=["metric", "dispatch_model", "excel_model"])
    df["delta"] = df["dispatch_model"] - df["excel_model"]
    df["delta_pct"] = df.apply(
        lambda r: (r["delta"] / r["excel_model"] * 100.0)
        if r["excel_model"] not in (0, None) and pd.notna(r["excel_model"])
        else float("nan"),
        axis=1,
    )
    return df


def revenue_components(dispatch: pd.DataFrame) -> pd.DataFrame:
    """Stacked BESS revenue by component for dispatch vs Excel.

    Arbitrage comes from the dispatch model; RA and ancillary services are
    contractual and identical across both (carried from config), so total BESS
    revenue is directly comparable.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        DataFrame indexed by component with ``dispatch_model`` and
        ``excel_model`` columns.
    """
    arb = metrics.annual_revenue(dispatch)
    data = {
        "dispatch_model": {
            "Arbitrage": arb,
            "Resource Adequacy": config.EXCEL_RA_REVENUE,
            "Ancillary Services": config.EXCEL_ANCILLARY_REVENUE,
        },
        "excel_model": {
            "Arbitrage": config.EXCEL_ARBITRAGE_REVENUE,
            "Resource Adequacy": config.EXCEL_RA_REVENUE,
            "Ancillary Services": config.EXCEL_ANCILLARY_REVENUE,
        },
    }
    df = pd.DataFrame(data)
    df.index.name = "component"
    return df


def variance_waterfall(dispatch: pd.DataFrame) -> pd.DataFrame:
    """Decompose arbitrage-revenue variance vs Excel into volume + price effects.

    Bridges Excel arbitrage revenue -> dispatch arbitrage revenue:

        volume effect = (disp_MWh - excel_MWh) * excel_spread
        price  effect = (disp_spread - excel_spread) * disp_MWh

    The two effects sum exactly to the total variance.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        DataFrame with ``label`` and ``amount`` ($) rows suitable for a Plotly
        waterfall (start, volume, price, end).
    """
    disp_mwh = metrics.total_discharge_mwh(dispatch)
    disp_spread = metrics.effective_spread(dispatch)
    excel_mwh = config.EXCEL_NET_DISCHARGE_MWH * config.EXCEL_ARBITRAGE_ALLOCATION
    excel_spread = config.EXCEL_EFFECTIVE_SPREAD

    volume_effect = (disp_mwh - excel_mwh) * excel_spread
    price_effect = (disp_spread - excel_spread) * disp_mwh

    rows = [
        ("Excel arbitrage", config.EXCEL_ARBITRAGE_REVENUE, "absolute"),
        ("Volume (cycles) effect", volume_effect, "relative"),
        ("Price (spread) effect", price_effect, "relative"),
        ("Dispatch arbitrage", metrics.annual_revenue(dispatch), "total"),
    ]
    return pd.DataFrame(rows, columns=["label", "amount", "measure"])


def duration_sensitivity(
    lmp_df: pd.DataFrame, durations_h: tuple[int, ...] = (2, 4, 6, 8)
) -> pd.DataFrame:
    """Annual arbitrage revenue vs BESS duration, holding power constant.

    Re-runs the greedy dispatch with energy capacity = power * duration for each
    duration. Power rating is fixed at the configured nameplate.

    Args:
        lmp_df: Hourly LMP DataFrame.
        durations_h: Durations (hours) to sweep.

    Returns:
        DataFrame with columns ``duration_h``, ``energy_mwh``, ``annual_revenue``,
        ``cycles_per_day``, ``effective_spread``.
    """
    rows = []
    for d in durations_h:
        energy = config.BESS_POWER_MW * d
        disp = run_dispatch(lmp_df, energy_mwh=energy, power_mw=config.BESS_POWER_MW)
        rows.append(
            {
                "duration_h": d,
                "energy_mwh": energy,
                "annual_revenue": metrics.annual_revenue(disp),
                "cycles_per_day": metrics.cycles_per_day(disp),
                "effective_spread": metrics.effective_spread(disp),
            }
        )
    return pd.DataFrame(rows)


def cycles_sensitivity(
    dispatch: pd.DataFrame,
    cycles: tuple[float, ...] = (1.0, 1.3, 1.5, 1.7),
) -> pd.DataFrame:
    """Annual arbitrage revenue vs cycles/day under the Excel linear methodology.

    The Excel model treats revenue as linear in cycles/day at a fixed spread.
    This reproduces that line (anchored on the $2.17M / 1.3-cycle point) and
    attaches the dispatch model's realized operating point for comparison.

    Args:
        dispatch: Hourly dispatch DataFrame (for the realized marker).
        cycles: Cycles/day values to evaluate.

    Returns:
        DataFrame with ``cycles_per_day``, ``excel_revenue``, and a single
        ``dispatch_revenue`` value placed at the dispatch's realized cycles.
    """
    rev_per_cycle = config.EXCEL_ARBITRAGE_REVENUE / config.EXCEL_CYCLES_PER_DAY
    df = pd.DataFrame({"cycles_per_day": list(cycles)})
    df["excel_revenue"] = df["cycles_per_day"] * rev_per_cycle
    df.attrs["dispatch_point"] = {
        "cycles_per_day": metrics.cycles_per_day(dispatch),
        "annual_revenue": metrics.annual_revenue(dispatch),
    }
    return df
