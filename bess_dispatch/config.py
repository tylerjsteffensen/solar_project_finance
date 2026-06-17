"""
Central configuration for the BESS hourly dispatch model.

Every tunable assumption lives here so the analysis can be re-parameterized
without editing any business logic. Import `config` (or the individual
constants) from any module:

    from bess_dispatch import config
    print(config.BESS_POWER_MW)

All monetary values are nominal USD, Year 1 only (no escalation), to stay
consistent with the "Year 1" column of the Excel project finance model.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PACKAGE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# --------------------------------------------------------------------------- #
# Analysis horizon
# --------------------------------------------------------------------------- #
DATA_YEAR = 2023          # Calendar year of CAISO LMP data to analyze
PROJECT_YEAR = 1          # Project year to model (degradation reference)

# --------------------------------------------------------------------------- #
# Solar resource
# --------------------------------------------------------------------------- #
SOLAR_MWAC = 100.0        # AC nameplate (MWac)
SOLAR_MWDC = 120.0        # DC nameplate (MWdc), DC:AC ratio 1.2
SOLAR_CAPACITY_FACTOR = 0.27   # P50 annual AC capacity factor
SOLAR_CURTAILMENT = 0.04       # Annual energy curtailment fraction
SOLAR_SEASONAL_SWING = 0.35    # +/- fractional summer/winter swing about the mean
SOLAR_DAILY_VARIABILITY = 0.18 # std-dev of day-to-day clear-sky multiplier (clouds)

# --------------------------------------------------------------------------- #
# Battery energy storage system (BESS)
# --------------------------------------------------------------------------- #
BESS_POWER_MW = 50.0          # Power rating (MW)
BESS_ENERGY_MWH = 200.0       # Energy capacity (MWh) -> 4-hour duration
BESS_RTE = 0.85               # Round-trip efficiency (applied on charge side)
BESS_DEGRADATION_PER_YEAR = 0.025   # 2.5%/yr, applied to BOTH power and energy

SOC_MIN_FRAC = 0.10           # Minimum state of charge (10% -> 20 MWh floor)
SOC_MAX_FRAC = 0.95           # Maximum state of charge (95% -> 190 MWh ceiling)
SOC_INITIAL_FRAC = 0.50       # Starting state of charge for the simulation

# --------------------------------------------------------------------------- #
# Resource Adequacy (RA)
# --------------------------------------------------------------------------- #
RA_CAPACITY_MW = 45.0         # MW that must remain available for discharge ...
RA_PEAK_HOURS = (16, 17, 18, 19, 20, 21)  # ... during these hours-ending (HE16-21)

# --------------------------------------------------------------------------- #
# Greedy dispatch signal
# --------------------------------------------------------------------------- #
ROLLING_WINDOW_DAYS = 30      # Trailing window for percentile price signal
CHARGE_PERCENTILE = 25        # Charge when LMP <= rolling 25th percentile
DISCHARGE_PERCENTILE = 75     # Discharge when LMP >= rolling 75th percentile

# --------------------------------------------------------------------------- #
# CAISO OASIS API
# --------------------------------------------------------------------------- #
# NOTE on report choice: PRC_LMP serves the *Day-Ahead Market* (DAM), which is
# natively hourly, allows 31-day request chunks, and is what the OASIS website's
# LMP Prices page exposes. This is the practical, default basis for the model.
#
# True real-time 5-minute LMP is a *different* report, PRC_INTVL_LMP with
# market_run_id=RTM. It is intentionally NOT the default: OASIS caps 5-minute
# queries at ~1 trade day per request (~100k rows/month), which is impractical
# to download manually for a full year. To use it anyway, set:
#     CAISO_QUERYNAME    = "PRC_INTVL_LMP"
#     CAISO_MARKET_RUN_ID = "RTM"
#     CAISO_VERSION       = 3
#     CAISO_CHUNK_DAYS    = 1
CAISO_BASE_URL = "https://oasis.caiso.com/oasisapi/SingleZip"
CAISO_QUERYNAME = "PRC_LMP"
CAISO_MARKET_RUN_ID = "DAM"          # Day-ahead market (hourly LMP)
CAISO_NODE = "TH_SP15_GEN-APND"      # SP15 generation trading hub APnode
CAISO_VERSION = 12
CAISO_RESULTFORMAT = 6           # 6 = CSV (zipped)
CAISO_CHUNK_DAYS = 31            # Max date span per OASIS request
CAISO_MAX_RETRIES = 4
CAISO_BACKOFF_BASE_SEC = 5       # Exponential backoff base (OASIS rate-limits hard)
CAISO_REQUEST_TIMEOUT = 60       # Seconds
CAISO_INTER_REQUEST_DELAY = 7    # Seconds between live chunk requests (AUP: >=5s)

# If the OASIS API is unreachable (firewall, 403 throttle, offline), the fetcher
# falls back to a deterministic synthetic SP15 price series so the rest of the
# pipeline and the dashboard remain fully runnable. Set to False to hard-fail
# instead of falling back.
ALLOW_SYNTHETIC_LMP_FALLBACK = True
SYNTHETIC_SEED = 2023            # RNG seed for reproducible synthetic data

# --------------------------------------------------------------------------- #
# Excel project finance model baseline (Year 1) -- the numbers we validate
# against. Sourced directly from the Revenue tab of
# Solar_Plus_Storage_PF_Model_POLISHED.xlsx.
# --------------------------------------------------------------------------- #
EXCEL_ARBITRAGE_REVENUE = 2_172_588.0   # BESS Energy Arbitrage Revenue ($)
EXCEL_EFFECTIVE_SPREAD = 38.48          # Gross Arbitrage Spread ($/MWh)
EXCEL_CYCLES_PER_DAY = 1.3              # Simplified dispatch assumption
EXCEL_ARBITRAGE_ALLOCATION = 0.70       # Capacity share allocated to arbitrage
EXCEL_DISCHARGE_PRICE = 65.0            # $/MWh discharge price assumption
EXCEL_PURCHASE_PRICE = 25.0             # $/MWh grid purchase price assumption
EXCEL_RA_REVENUE = 3_240_000.0          # Resource Adequacy revenue ($)
EXCEL_ANCILLARY_REVENUE = 1_125_000.0   # Ancillary services revenue ($)
EXCEL_NET_DISCHARGE_MWH = 80_665.0      # Total BESS net discharge (MWh)
EXCEL_TOTAL_BESS_REVENUE = (
    EXCEL_ARBITRAGE_REVENUE + EXCEL_RA_REVENUE + EXCEL_ANCILLARY_REVENUE
)


# --------------------------------------------------------------------------- #
# Derived helpers
# --------------------------------------------------------------------------- #
def degradation_factor(year: int = PROJECT_YEAR) -> float:
    """Return the BESS capacity multiplier for a given project year.

    Year 1 is nameplate (factor 1.0); each subsequent year compounds the
    annual degradation rate. Applied identically to power and energy, matching
    the Excel model's single-rate convention.

    Args:
        year: Project year (1-indexed).

    Returns:
        Multiplier in (0, 1].
    """
    return (1.0 - BESS_DEGRADATION_PER_YEAR) ** (year - 1)


def degraded_power_mw(year: int = PROJECT_YEAR) -> float:
    """Power rating (MW) after degradation for the given project year."""
    return BESS_POWER_MW * degradation_factor(year)


def degraded_energy_mwh(year: int = PROJECT_YEAR) -> float:
    """Energy capacity (MWh) after degradation for the given project year."""
    return BESS_ENERGY_MWH * degradation_factor(year)


def soc_floor_mwh(energy_mwh: float = BESS_ENERGY_MWH) -> float:
    """Absolute SoC floor (MWh) for a given (possibly degraded) capacity."""
    return SOC_MIN_FRAC * energy_mwh


def soc_ceiling_mwh(energy_mwh: float = BESS_ENERGY_MWH) -> float:
    """Absolute SoC ceiling (MWh) for a given (possibly degraded) capacity."""
    return SOC_MAX_FRAC * energy_mwh
