# BESS Hourly Dispatch — CAISO SP15

Hourly battery dispatch optimization for a utility-scale solar-plus-storage
project in **CAISO SP15**, built to validate (and stress-test) the simplified
annual BESS-revenue assumptions in the companion Excel project finance model
(`Solar_Plus_Storage_PF_Model_POLISHED.xlsx`).

It pulls real CAISO SP15 day-ahead LMP data, generates a synthetic solar
profile, runs a transparent **greedy** dispatch against the price curve, and
compares the resulting arbitrage revenue, captured spread, and cycle count
against the Excel model's flat assumptions — all in an interactive Streamlit
dashboard.

---

## Project specification

| Parameter | Value |
|---|---|
| Solar | 100 MWac / 120 MWdc, single-axis tracking, 27% P50 CF |
| BESS power | 50 MW |
| BESS energy | 200 MWh (4-hour duration) |
| Round-trip efficiency | 85% (loss booked on charge side) |
| SoC band | 10% floor (20 MWh) – 95% ceiling (190 MWh) |
| Degradation | 2.5%/yr on power **and** energy |
| Resource Adequacy | 45 MW held available for discharge, HE16–21 |
| Location | CAISO SP15 (Southern California) |
| Price node | `TH_SP15_GEN-APND`, day-ahead market (DAM) |

All monetary values are **nominal USD, Year 1 only** (no escalation), to match
the Year-1 column of the Excel model.

---

## Quick start

```bash
# from the repository root (the folder containing the bess_dispatch/ package)
pip install -r bess_dispatch/requirements.txt

# launch the dashboard (single command deliverable)
streamlit run bess_dispatch/dashboard/app.py
```

The dashboard loads cached CAISO data if present, otherwise fetches and caches
it on first run. You can also run the pipeline headless:

```bash
python -m bess_dispatch.main            # prints a console report
python -m bess_dispatch.main --refresh  # ignore caches, re-fetch from OASIS
python -m bess_dispatch.tests.test_dispatch   # run the invariant test suite
```

> **Windows note (HP OMEN, Python 3.10+):** the commands above are identical in
> PowerShell. If `streamlit` isn't found, use `python -m streamlit run
> bess_dispatch/dashboard/app.py`.

### Offline / firewalled environments

CAISO OASIS aggressively rate-limits unauthenticated callers (HTTP 403) and is
often blocked behind corporate or sandbox firewalls. When the API is
unreachable, the fetcher **automatically falls back to a deterministic,
realistic synthetic SP15 price series** (duck-curve shape, seasonal envelope,
evening scarcity spikes) so the entire pipeline and dashboard remain runnable.
The dashboard shows a clear banner when synthetic data is in use. Set
`ALLOW_SYNTHETIC_LMP_FALLBACK = False` in `config.py` to hard-fail instead.

### Loading real data manually (recommended when the API is throttled)

OASIS throttles programmatic callers but serves downloads fine to a browser.
Download the day-ahead LMP files yourself and let the ingest tool build the
cache:

1. Create a download folder:
   ```
   mkdir bess_dispatch/data/cache/manual_downloads
   ```
2. For each month, paste a URL like this into your browser (it downloads a
   `.zip` — save it into that folder, no need to unzip). Change only the two
   dates to march through the year in ≤31-day chunks:
   ```
   https://oasis.caiso.com/oasisapi/SingleZip?resultformat=6&queryname=PRC_LMP&version=1&market_run_id=DAM&node=TH_SP15_GEN-APND&startdatetime=20250101T08:00-0000&enddatetime=20250201T08:00-0000
   ```
3. Convert the downloads into the dashboard's cache:
   ```
   python -m bess_dispatch.data.ingest_manual --year 2025
   ```
   (accepts `.zip` or `.csv`; add `--src "C:\path"` if files are elsewhere).
4. Launch the dashboard — the banner now confirms real CAISO data.

> **Why day-ahead (DAM) and not 5-minute real-time?** `PRC_LMP` is the
> day-ahead report — natively hourly (matches this model), 31-day chunks, and
> exactly what the OASIS LMP Prices page exposes. True 5-minute real-time
> (`PRC_INTVL_LMP` / `RTM`) is capped at ~1 trade day per request (~100k
> rows/month), impractical to pull for a full year. Switch reports via the
> `CAISO_*` settings in `config.py` if you do want real-time.

---

## File structure

```
bess_dispatch/
  config.py              # ALL tunable assumptions (single source of truth)
  main.py                # entry point — runs the full pipeline + console report
  data/
    caiso_fetch.py       # CAISO OASIS API client: chunking, retries, caching, fallback
    solar_gen.py         # synthetic solar generation (NSRDB-ready interface)
    cache/               # cached OASIS responses (gitignored)
  dispatch/
    optimizer.py         # greedy hourly dispatch algorithm
    constraints.py       # SoC, power, RTE, and RA constraint math
  analysis/
    metrics.py           # revenue, spread, cycles, RA-binding, capacity factor
    comparison.py        # Excel-vs-dispatch comparison + sensitivity sweeps
  dashboard/
    app.py               # Streamlit dashboard (4 panels)
  tests/
    test_dispatch.py     # physical-invariant tests (run offline)
  requirements.txt
  README.md
```

Every module is importable independently, e.g.:

```python
from bess_dispatch.dispatch.optimizer import run_dispatch
from bess_dispatch.data.caiso_fetch import get_hourly_lmp
disp = run_dispatch(get_hourly_lmp(2023))
```

---

## The dispatch algorithm

A deliberately simple, fully transparent **greedy** rule-based dispatcher — no
linear/mixed-integer programming. Each hour:

1. **Price signal.** Compute the trailing **30-day rolling 35th and 65th
   percentile** of LMP (causal — no look-ahead).
2. **Charge** when `LMP ≤ 35th percentile`, up to the lesser of the power
   rating and the headroom to the 95% ceiling. Stored energy = grid energy ×
   RTE (the round-trip loss is booked entirely on the charge side).
3. **Discharge** when `LMP ≥ 65th percentile`, up to the lesser of the power
   rating and the energy available above the 10% floor. Discharge is loss-free.
4. **Otherwise idle.**
5. **Resource Adequacy.** During hours-ending 16–21, 45 MW of power is reserved
   for the RA obligation, so charging in those hours is throttled to 5 MW
   (50 − 45). Discharging satisfies RA, so it is unconstrained. Hours where the
   reservation actually binds are flagged for reporting.
6. **State of charge** is tracked hour by hour and clamped to `[10%, 95%]`.

Revenue is pure arbitrage: `Σ (discharge_MWh × LMP) − (charge_MWh × LMP)`.
RA ($3.24M) and ancillary-services ($1.13M) revenue are contractual and carried
through from the Excel model unchanged.

### Why greedy, and how it differs from a proper LP

The greedy heuristic is easy to audit and fast, but it leaves value on the
table relative to a true optimization:

| | Greedy (this tool) | LP / MIP optimum |
|---|---|---|
| Foresight | None — reacts to a trailing percentile signal | Perfect (or forecast) foresight over a horizon |
| Cycle timing | May charge/discharge sub-optimally near thresholds | Globally optimal charge/discharge scheduling |
| Threshold choice | Fixed 35th/65th percentiles | Endogenous — dispatches whenever marginal spread > marginal cost |
| Degradation cost | Not priced into the decision | Can co-optimize throughput vs. degradation |
| Multi-service | Arbitrage only; RA handled as a hard reserve | Can co-optimize energy + AS + RA capacity |
| Typical result | **Captures wider spreads but fewer cycles** | More cycles at slightly thinner average spread, higher total $ |

In practice the greedy model tends to **over-state captured spread and
under-state cycle count** versus both the Excel 1.3-cycle assumption and a real
LP — it only acts on the most extreme price hours. The duration and cycles
sensitivity panels are there precisely to bound this. Treat the output as a
**directional validation** of the Excel arbitrage line, not a bankable P50.

---

## Dashboard panels

1. **CAISO SP15 LMP profile** — monthly average heatmap (hour × month), annual
   price-duration curve, and average daily shape by season.
2. **BESS dispatch profile** — a selectable week of hourly SoC / charge /
   discharge / LMP on a dual axis, plus a monthly charge-discharge-revenue
   summary.
3. **Revenue comparison** — BESS revenue by component (dispatch vs Excel), an
   arbitrage-variance waterfall (volume vs price effects), and a key-metrics
   table.
4. **Sensitivity** — annual revenue vs BESS duration (2/4/6/8h, power held at
   50 MW, dispatch re-run each time) and vs cycles/day (1.0/1.3/1.5/1.7, Excel
   linear methodology) with the dispatch model's realized operating point.

---

## Configuration

Every assumption — power rating, energy capacity, RTE, SoC limits, RA capacity
and window, percentile thresholds, rolling window, CAISO node, and the Excel
baseline figures — lives in **`config.py`**. Change it there once; no business
logic edits required.

---

## Data sources & roadmap

- **CAISO OASIS** `PRC_LMP` / DAM / `TH_SP15_GEN-APND` — day-ahead hourly LMP.
  (True 5-minute real-time is `PRC_INTVL_LMP` / RTM — configurable, but capped
  at ~1 day per request, so impractical to download for a full year.)
- **Solar** is currently synthetic (sinusoidal clear-sky + seasonal envelope +
  daily cloud variability, scaled to 27% CF, 4% curtailment). The
  `solar_from_nsrdb()` stub in `solar_gen.py` marks the drop-in point for real
  **NREL NSRDB (PSM3)** irradiance in a future version.

### Known limitations

- Greedy dispatch, not LP/MIP (see table above).
- Synthetic solar; solar is contextual and not co-optimized with the battery
  (the battery charges from the grid, consistent with the Excel arbitrage line).
- Single 2.5%/yr degradation rate applied identically to power and energy.
- Year 1 only; no price escalation, augmentation, or multi-year horizon.
- RA modeled as a power reservation in the peak window, not a full 4-hour
  sustained-capacity accreditation test.
