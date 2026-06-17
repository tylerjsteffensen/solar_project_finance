"""
Greedy hourly BESS dispatch optimizer.

A deliberately simple, transparent rule-based dispatcher (no LP/MIP):

* Charge when the LMP is at/below the trailing 30-day 25th percentile.
* Discharge when the LMP is at/above the trailing 30-day 75th percentile.
* Otherwise idle.

State of charge is tracked hour by hour and clamped to the [10%, 95%] band.
Round-trip-efficiency losses are booked on the charge side only (stored energy
= grid charge * RTE; discharge is loss-free). During the RA peak window
(HE16-21) 45 MW of power is reserved so the battery can always answer its RA
nomination, which constrains how much it may charge in those hours.

Revenue is purely arbitrage: ``discharge_mwh * lmp - charge_mwh * lmp``. RA and
ancillary-services revenue are contractual and handled in the comparison module,
not here.

See :mod:`bess_dispatch.dispatch.constraints` for the per-hour limit math.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from . import constraints
from .constraints import BatteryLimits


def _rolling_percentiles(lmp: pd.Series, window_hours: int) -> tuple[pd.Series, pd.Series]:
    """Trailing rolling charge/discharge price thresholds.

    Uses a trailing window of ``window_hours`` (min 24h so early days still get a
    signal). Returns the configured charge and discharge percentiles. The window
    is causal (no look-ahead): hour t uses only hours <= t.

    Args:
        lmp: Hourly LMP series.
        window_hours: Trailing window length in hours.

    Returns:
        Tuple of (charge_threshold, discharge_threshold) series aligned to lmp.
    """
    roll = lmp.rolling(window=window_hours, min_periods=24)
    lo = roll.quantile(config.CHARGE_PERCENTILE / 100.0)
    hi = roll.quantile(config.DISCHARGE_PERCENTILE / 100.0)
    # Backfill the first <24h so hour 0 has a usable (if coarse) threshold.
    lo = lo.bfill()
    hi = hi.bfill()
    return lo, hi


def run_dispatch(
    lmp_df: pd.DataFrame,
    *,
    year: int = config.PROJECT_YEAR,
    power_mw: float | None = None,
    energy_mwh: float | None = None,
    soc_init_frac: float = config.SOC_INITIAL_FRAC,
) -> pd.DataFrame:
    """Run the greedy dispatch over an hourly LMP series.

    Args:
        lmp_df: DataFrame indexed by hourly timestamp with an ``lmp`` column.
        year: Project year (sets degradation-adjusted capacity).
        power_mw: Override nameplate power (MW) -- used by sensitivity sweeps.
        energy_mwh: Override nameplate energy (MWh) -- used by sensitivity sweeps.
        soc_init_frac: Initial state of charge as a fraction of capacity.

    Returns:
        Hourly DataFrame indexed by timestamp with columns:
            ``lmp``, ``charge_mw``, ``discharge_mw``, ``soc_mwh``, ``soc_pct``,
            ``revenue``, ``ra_binding`` (bool), ``is_peak`` (bool).
    """
    limits = BatteryLimits.for_year(year, power_mw=power_mw, energy_mwh=energy_mwh)

    lmp = lmp_df["lmp"].astype(float)
    window_hours = config.ROLLING_WINDOW_DAYS * 24
    charge_thr, discharge_thr = _rolling_percentiles(lmp, window_hours)

    timestamps = lmp.index
    prices = lmp.to_numpy()
    lo = charge_thr.to_numpy()
    hi = discharge_thr.to_numpy()
    # Hour-ending label: clock hour 15 -> HE16, so HE = clock_hour + 1.
    hours_ending = (timestamps.hour.to_numpy() + 1)

    n = len(prices)
    charge_mw = np.zeros(n)
    discharge_mw = np.zeros(n)
    soc_series = np.zeros(n)
    revenue = np.zeros(n)
    ra_binding = np.zeros(n, dtype=bool)

    soc = soc_init_frac * limits.energy_mwh

    for i in range(n):
        price = prices[i]
        peak = constraints.is_peak_hour(hours_ending[i])

        if peak:
            # Flag the RA constraint as binding whenever (a) the battery cannot
            # currently back its full 45 MW nomination, or (b) a charge signal is
            # throttled by the reserved RA power.
            shortfall = constraints.ra_shortfall_mw(soc, limits)
            if shortfall > 1e-6:
                ra_binding[i] = True

        if price <= lo[i]:
            # Charge (buy low).
            qty = constraints.max_charge_mwh(soc, limits, peak=peak)
            if peak and qty < constraints.max_charge_mwh(
                soc, limits, peak=False
            ) - 1e-6:
                ra_binding[i] = True  # RA reservation throttled an intended charge
            charge_mw[i] = qty
            soc += qty * limits.rte
            revenue[i] = -qty * price

        elif price >= hi[i]:
            # Discharge (sell high).
            qty = constraints.max_discharge_mwh(soc, limits, peak=peak)
            discharge_mw[i] = qty
            soc -= qty
            revenue[i] = qty * price

        # else: idle.

        soc_series[i] = soc

    out = pd.DataFrame(
        {
            "lmp": prices,
            "charge_mw": charge_mw,
            "discharge_mw": discharge_mw,
            "soc_mwh": soc_series,
            "soc_pct": soc_series / limits.energy_mwh * 100.0,
            "revenue": revenue,
            "ra_binding": ra_binding,
            "is_peak": np.isin(hours_ending, config.RA_PEAK_HOURS),
        },
        index=timestamps,
    )
    out.index.name = "timestamp"
    out.attrs["limits"] = limits
    return out


if __name__ == "__main__":
    from ..data.caiso_fetch import get_hourly_lmp

    disp = run_dispatch(get_hourly_lmp())
    print(f"annual arbitrage revenue = ${disp['revenue'].sum():,.0f}")
    print(f"discharged = {disp['discharge_mw'].sum():,.0f} MWh, "
          f"charged = {disp['charge_mw'].sum():,.0f} MWh")
