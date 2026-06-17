"""
Operational constraints for the BESS dispatch model.

Encapsulates the physical and contractual limits the greedy optimizer must
respect every hour:

* State-of-charge floor (10%) and ceiling (95%).
* Power-rating limits on charge and discharge.
* Round-trip-efficiency accounting (losses booked on the charge side only).
* Resource Adequacy: 45 MW must remain available for discharge during the
  evening peak window (hours-ending 16-21).

Each function is pure and importable on its own. ``BatteryLimits`` bundles the
(possibly degradation-adjusted) capacity parameters for a given project year.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class BatteryLimits:
    """Resolved battery limits for a single project year / scenario.

    Attributes:
        power_mw: Max charge/discharge power (MW), after degradation.
        energy_mwh: Usable energy capacity nameplate (MWh), after degradation.
        rte: Round-trip efficiency (charge-side loss).
        soc_floor_mwh: Absolute minimum state of charge (MWh).
        soc_ceiling_mwh: Absolute maximum state of charge (MWh).
        ra_capacity_mw: MW reserved for RA during the peak window.
    """

    power_mw: float
    energy_mwh: float
    rte: float
    soc_floor_mwh: float
    soc_ceiling_mwh: float
    ra_capacity_mw: float

    @classmethod
    def for_year(
        cls,
        year: int = config.PROJECT_YEAR,
        *,
        power_mw: float | None = None,
        energy_mwh: float | None = None,
    ) -> "BatteryLimits":
        """Build limits for a project year, optionally overriding nameplate.

        Overrides (used by sensitivity sweeps, e.g. varying duration) are applied
        before degradation.

        Args:
            year: Project year (1-indexed); degradation reference.
            power_mw: Override nameplate power (MW) pre-degradation.
            energy_mwh: Override nameplate energy (MWh) pre-degradation.

        Returns:
            A populated ``BatteryLimits`` instance.
        """
        deg = config.degradation_factor(year)
        p = (power_mw if power_mw is not None else config.BESS_POWER_MW) * deg
        e = (energy_mwh if energy_mwh is not None else config.BESS_ENERGY_MWH) * deg
        return cls(
            power_mw=p,
            energy_mwh=e,
            rte=config.BESS_RTE,
            soc_floor_mwh=config.SOC_MIN_FRAC * e,
            soc_ceiling_mwh=config.SOC_MAX_FRAC * e,
            ra_capacity_mw=config.RA_CAPACITY_MW,
        )


def is_peak_hour(hour_ending: int) -> bool:
    """Return True if an hour falls in the RA peak window (HE16-21).

    Args:
        hour_ending: Hour-ending label (1-24). HE16 covers clock 15:00-16:00.

    Returns:
        Whether the hour is within ``config.RA_PEAK_HOURS``.
    """
    return hour_ending in config.RA_PEAK_HOURS


def max_charge_mwh(soc_mwh: float, limits: BatteryLimits, *, peak: bool) -> float:
    """Max grid-side energy (MWh) the battery may charge this hour.

    Bounded by the lesser of (a) power rating and (b) headroom to the ceiling,
    where headroom is grossed up by 1/RTE because only ``charge * RTE`` is
    stored. During the RA peak window, charging may not consume the reserved RA
    power, so charge power is limited to ``power - ra_capacity``.

    Args:
        soc_mwh: Current state of charge (MWh).
        limits: Resolved battery limits.
        peak: Whether this is an RA peak hour.

    Returns:
        Maximum grid-side charge energy for the hour (MWh, >= 0).
    """
    power_cap = limits.power_mw
    if peak:
        power_cap = max(0.0, limits.power_mw - limits.ra_capacity_mw)
    headroom_stored = max(0.0, limits.soc_ceiling_mwh - soc_mwh)
    headroom_grid = headroom_stored / limits.rte if limits.rte else 0.0
    return max(0.0, min(power_cap, headroom_grid))


def max_discharge_mwh(soc_mwh: float, limits: BatteryLimits, *, peak: bool) -> float:
    """Max delivered energy (MWh) the battery may discharge this hour.

    Bounded by the lesser of (a) power rating and (b) energy available above the
    floor. Discharging during the peak window *satisfies* the RA obligation, so
    full power is available; however energy is kept at/above the floor at all
    times.

    Args:
        soc_mwh: Current state of charge (MWh).
        limits: Resolved battery limits.
        peak: Whether this is an RA peak hour (unused for the cap, kept for
            signature symmetry / future tuning).

    Returns:
        Maximum delivered discharge energy for the hour (MWh, >= 0).
    """
    available = max(0.0, soc_mwh - limits.soc_floor_mwh)
    return max(0.0, min(limits.power_mw, available))


def ra_shortfall_mw(soc_mwh: float, limits: BatteryLimits) -> float:
    """RA capacity shortfall (MW) given current state of charge.

    The RA obligation requires 45 MW be deliverable. Deliverable power is the
    lesser of the power rating and the energy above the floor (per 1-hour block).
    A positive return means the battery cannot currently back its full RA
    nomination -- used to flag binding hours.

    Args:
        soc_mwh: Current state of charge (MWh).
        limits: Resolved battery limits.

    Returns:
        Shortfall in MW (0 if the obligation is fully met).
    """
    deliverable = min(limits.power_mw, max(0.0, soc_mwh - limits.soc_floor_mwh))
    return max(0.0, limits.ra_capacity_mw - deliverable)
