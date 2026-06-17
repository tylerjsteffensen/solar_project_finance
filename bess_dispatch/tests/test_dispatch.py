"""
Invariant and sanity tests for the BESS dispatch pipeline.

Run with:  python -m bess_dispatch.tests.test_dispatch
(or under pytest:  pytest bess_dispatch/tests)

These tests use the synthetic LMP fallback so they run fully offline.
"""

from __future__ import annotations

import numpy as np

from .. import config
from ..analysis import metrics
from ..data.caiso_fetch import generate_synthetic_lmp
from ..data.solar_gen import generate_solar
from ..dispatch.constraints import BatteryLimits
from ..dispatch.optimizer import run_dispatch

TOL = 1e-6


def _dispatch():
    """Run a dispatch on the deterministic synthetic price series."""
    return run_dispatch(generate_synthetic_lmp(config.DATA_YEAR))


def test_soc_band_respected():
    """State of charge never breaches the 10% floor or 95% ceiling."""
    d = _dispatch()
    lim: BatteryLimits = d.attrs["limits"]
    assert d["soc_mwh"].min() >= lim.soc_floor_mwh - TOL
    assert d["soc_mwh"].max() <= lim.soc_ceiling_mwh + TOL


def test_power_limits_respected():
    """Charge and discharge never exceed the (degraded) power rating."""
    d = _dispatch()
    lim: BatteryLimits = d.attrs["limits"]
    assert (d["charge_mw"] <= lim.power_mw + TOL).all()
    assert (d["discharge_mw"] <= lim.power_mw + TOL).all()


def test_no_simultaneous_charge_discharge():
    """The battery never charges and discharges in the same hour."""
    d = _dispatch()
    assert int(((d["charge_mw"] > 0) & (d["discharge_mw"] > 0)).sum()) == 0


def test_ra_reservation_throttles_peak_charging():
    """During HE16-21 charging is capped at (power - RA reserve)."""
    d = _dispatch()
    lim: BatteryLimits = d.attrs["limits"]
    peak = d[d["is_peak"]]
    assert peak["charge_mw"].max() <= lim.power_mw - lim.ra_capacity_mw + TOL


def test_rte_energy_balance():
    """Net SoC change equals stored charge (× RTE) minus discharge."""
    d = _dispatch()
    lim: BatteryLimits = d.attrs["limits"]
    stored = d["charge_mw"].sum() * lim.rte - d["discharge_mw"].sum()
    soc_change = d["soc_mwh"].iloc[-1] - config.SOC_INITIAL_FRAC * lim.energy_mwh
    assert abs(stored - soc_change) < 1.0  # MWh tolerance over a full year


def test_metrics_nonnegative_and_sane():
    """Headline metrics are finite and within plausible ranges."""
    d = _dispatch()
    m = metrics.summarize(d)
    assert m["annual_revenue"] > 0
    assert 0 < m["effective_spread"] < 500
    assert 0 < m["cycles_per_day"] < 5
    assert 0 <= m["ra_binding_pct"] <= 100


def test_solar_capacity_factor():
    """Synthetic solar lands near the target CF after curtailment."""
    s = generate_solar(config.DATA_YEAR)
    cf = s["solar_mw"].sum() / (config.SOLAR_MWAC * len(s))
    target = config.SOLAR_CAPACITY_FACTOR * (1 - config.SOLAR_CURTAILMENT)
    assert abs(cf - target) < 0.02


def _run_all():
    """Execute every test function and report pass/fail (no pytest required)."""
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:  # noqa: PERF203
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
