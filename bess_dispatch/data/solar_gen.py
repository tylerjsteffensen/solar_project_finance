"""
Synthetic solar production generator.

Produces an hourly AC generation series (MW) for a single-axis-tracking PV plant
using a simple sinusoidal clear-sky daily profile, scaled so the annual energy
matches the target P50 capacity factor, with a seasonal envelope and random
day-to-day variability (clouds). A flat annual curtailment haircut is applied.

This is intentionally lightweight and is designed to be swapped for an
NREL NSRDB (PSM3) based profile in a future version -- see
``solar_from_nsrdb`` for the drop-in signature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


def generate_solar(
    year: int = config.DATA_YEAR,
    *,
    capacity_factor: float = config.SOLAR_CAPACITY_FACTOR,
    curtailment: float = config.SOLAR_CURTAILMENT,
    seed: int = config.SYNTHETIC_SEED,
) -> pd.DataFrame:
    """Generate an hourly synthetic solar AC production series for one year.

    The daily shape is a clipped sinusoid centered on solar noon whose width
    varies seasonally (longer summer days). Energy is then rescaled so the
    post-curtailment annual capacity factor equals ``capacity_factor``.

    Args:
        year: Calendar year (controls length / leap year).
        capacity_factor: Target annual AC capacity factor (post-curtailment).
        curtailment: Fractional annual energy curtailment (0-1).
        seed: RNG seed for reproducible day-to-day variability.

    Returns:
        DataFrame indexed by tz-naive hourly ``timestamp`` with columns
        ``solar_mw`` (AC MW, after curtailment) and ``solar_mw_gross``
        (before curtailment).
    """
    rng = np.random.default_rng(seed + year)
    index = pd.date_range(f"{year}-01-01 00:00", f"{year}-12-31 23:00", freq="1h")

    hour = index.hour.to_numpy().astype(float)
    doy = index.dayofyear.to_numpy()

    # Seasonal daylight: half-day-length swings from ~5.0h (winter) to ~7.0h
    # (summer) about solar noon (~13:00 local with DST-ish simplification).
    solar_noon = 13.0
    half_day = 6.0 + 1.0 * np.cos(2 * np.pi * (doy - 172) / 365)  # peak ~Jun 21

    # Clear-sky cosine bell, zero outside daylight window.
    x = (hour - solar_noon) / half_day
    clear_sky = np.cos(0.5 * np.pi * x)
    clear_sky = np.where(np.abs(x) <= 1.0, clear_sky, 0.0)
    clear_sky = np.clip(clear_sky, 0.0, None)

    # Seasonal peak-irradiance envelope (summer brighter than winter).
    seasonal_amp = 1.0 + config.SOLAR_SEASONAL_SWING * np.cos(2 * np.pi * (doy - 172) / 365)
    profile = clear_sky * seasonal_amp

    # Day-to-day cloud variability: one multiplier per day, applied to all hours.
    n_days = (index[-1].normalize() - index[0].normalize()).days + 1
    day_mult = rng.normal(1.0, config.SOLAR_DAILY_VARIABILITY, size=n_days).clip(0.1, 1.3)
    day_index = (index.normalize() - index[0].normalize()).days
    profile = profile * day_mult[day_index]

    # Scale to nameplate then rescale energy to hit the target capacity factor.
    raw_mw = profile * config.SOLAR_MWAC
    raw_mw = np.clip(raw_mw, 0.0, config.SOLAR_MWAC)  # AC inverter clipping

    target_annual_mwh = capacity_factor * config.SOLAR_MWAC * len(index)
    current_annual_mwh = raw_mw.sum()
    scale = target_annual_mwh / current_annual_mwh if current_annual_mwh else 0.0
    gross_mw = np.clip(raw_mw * scale, 0.0, config.SOLAR_MWAC)

    net_mw = gross_mw * (1.0 - curtailment)

    out = pd.DataFrame(
        {"solar_mw": net_mw, "solar_mw_gross": gross_mw}, index=index
    )
    out.index.name = "timestamp"
    return out


def solar_from_nsrdb(*args, **kwargs):  # pragma: no cover - future hook
    """Placeholder for an NREL NSRDB (PSM3) backed solar profile.

    Intended drop-in replacement for :func:`generate_solar` with the same return
    contract. A future implementation would pull GHI/DNI/DHI for the site lat/lon
    from the NSRDB API, run a PVWatts-style AC model, and resample to hourly.

    Raises:
        NotImplementedError: Always, until NSRDB integration is added.
    """
    raise NotImplementedError(
        "NSRDB-backed solar generation is not yet implemented. "
        "Use generate_solar() for the synthetic profile."
    )


if __name__ == "__main__":
    df = generate_solar()
    cf = df["solar_mw"].sum() / (config.SOLAR_MWAC * len(df))
    print(f"rows={len(df)} realized CF (post-curtailment)={cf:.3f}")
    print(f"annual energy = {df['solar_mw'].sum():,.0f} MWh")
