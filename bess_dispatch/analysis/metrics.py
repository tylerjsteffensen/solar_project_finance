"""
Key performance metrics for a dispatch result.

Turns the hour-by-hour dispatch DataFrame from
:func:`bess_dispatch.dispatch.optimizer.run_dispatch` into the headline numbers
used by the comparison module and the dashboard: annual revenue, effective
captured spread, cycles per day, RA-binding frequency, BESS capacity factor, and
the charge/discharge price distributions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


def annual_revenue(dispatch: pd.DataFrame) -> float:
    """Total annual gross arbitrage revenue ($)."""
    return float(dispatch["revenue"].sum())


def total_discharge_mwh(dispatch: pd.DataFrame) -> float:
    """Total energy delivered to the grid over the year (MWh)."""
    return float(dispatch["discharge_mw"].sum())


def total_charge_mwh(dispatch: pd.DataFrame) -> float:
    """Total grid energy drawn for charging over the year (MWh)."""
    return float(dispatch["charge_mw"].sum())


def effective_spread(dispatch: pd.DataFrame) -> float:
    """Average $/MWh spread captured per unit discharged.

    Defined as total arbitrage revenue divided by total discharged energy, which
    is directly comparable to the Excel model's "Gross Arbitrage Spread"
    ($38.48/MWh). Returns 0 if nothing was discharged.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        Effective captured spread ($/MWh).
    """
    discharged = total_discharge_mwh(dispatch)
    return annual_revenue(dispatch) / discharged if discharged else 0.0


def cycles_per_day(dispatch: pd.DataFrame, energy_mwh: float | None = None) -> float:
    """Average equivalent full cycles per day.

    One equivalent full cycle = ``energy_mwh`` discharged. Uses the degraded
    capacity attached to the dispatch result when available.

    Args:
        dispatch: Hourly dispatch DataFrame.
        energy_mwh: Energy capacity to normalize by; defaults to the dispatch's
            own limits, falling back to nameplate.

    Returns:
        Equivalent full cycles per day.
    """
    if energy_mwh is None:
        limits = dispatch.attrs.get("limits")
        energy_mwh = limits.energy_mwh if limits else config.BESS_ENERGY_MWH
    n_days = len(dispatch) / 24.0
    if not energy_mwh or not n_days:
        return 0.0
    return total_discharge_mwh(dispatch) / energy_mwh / n_days


def ra_binding_pct(dispatch: pd.DataFrame) -> float:
    """Percentage of RA peak-window hours where the RA constraint was binding.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        Share (0-100) of peak hours flagged ``ra_binding``.
    """
    peak = dispatch[dispatch["is_peak"]]
    if peak.empty:
        return 0.0
    return float(peak["ra_binding"].mean() * 100.0)


def bess_capacity_factor(dispatch: pd.DataFrame) -> float:
    """BESS capacity factor based on discharge throughput.

    Discharged energy divided by (power rating * hours), expressed as a fraction.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        Capacity factor (0-1).
    """
    limits = dispatch.attrs.get("limits")
    power_mw = limits.power_mw if limits else config.BESS_POWER_MW
    denom = power_mw * len(dispatch)
    return total_discharge_mwh(dispatch) / denom if denom else 0.0


def charge_discharge_prices(dispatch: pd.DataFrame) -> dict[str, np.ndarray]:
    """Extract the LMPs at which the battery actually charged / discharged.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        Dict with ``charge_lmps`` and ``discharge_lmps`` (price arrays for hours
        with non-zero charge/discharge respectively).
    """
    return {
        "charge_lmps": dispatch.loc[dispatch["charge_mw"] > 0, "lmp"].to_numpy(),
        "discharge_lmps": dispatch.loc[dispatch["discharge_mw"] > 0, "lmp"].to_numpy(),
    }


def monthly_summary(dispatch: pd.DataFrame) -> pd.DataFrame:
    """Monthly totals of charge, discharge, and net arbitrage revenue.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        DataFrame indexed by month number (1-12) with columns
        ``charge_mwh``, ``discharge_mwh``, ``revenue``.
    """
    g = dispatch.groupby(dispatch.index.month)
    out = pd.DataFrame(
        {
            "charge_mwh": g["charge_mw"].sum(),
            "discharge_mwh": g["discharge_mw"].sum(),
            "revenue": g["revenue"].sum(),
        }
    )
    out.index.name = "month"
    return out


def summarize(dispatch: pd.DataFrame) -> dict[str, float]:
    """Bundle the headline metrics into a single dict.

    Args:
        dispatch: Hourly dispatch DataFrame.

    Returns:
        Dict of metric name -> value.
    """
    return {
        "annual_revenue": annual_revenue(dispatch),
        "effective_spread": effective_spread(dispatch),
        "cycles_per_day": cycles_per_day(dispatch),
        "ra_binding_pct": ra_binding_pct(dispatch),
        "bess_capacity_factor": bess_capacity_factor(dispatch),
        "total_discharge_mwh": total_discharge_mwh(dispatch),
        "total_charge_mwh": total_charge_mwh(dispatch),
    }
