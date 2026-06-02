# Solar-Plus-Storage Project Finance Model
### 100 MWac Solar PV + 50 MW / 200 MWh BESS · CAISO SP15 · April 2026

---

## Overview

A ground-up project finance model for a hypothetical utility-scale solar-plus-storage facility in CAISO SP15 (Southern California). Built as a portfolio project targeting developer analyst roles at independent power producers and utility-scale developers.

The model covers the full analytical stack a developer analyst would use to evaluate a real project: energy production, multi-stream revenue modeling, debt sizing, IRA tax credit mechanics, LCOE benchmarking, and sensitivity analysis — all sourced to current public benchmarks.

---

## Project Specification

| Parameter | Value |
|---|---|
| Technology | Single-axis tracking PV + AC-coupled Li-ion BESS |
| Solar Capacity | 100 MWac / 120 MWdc (DC:AC 1.2x) |
| Storage Capacity | 50 MW / 200 MWh (4-hour duration) |
| Location | CAISO SP15 — Southern California / Mojave region |
| P50 Capacity Factor | 27% AC · P90: 24% AC |
| Project Life | 30 years · COD: Year 1 (simplified) |
| PPA | $50/MWh · 1.0%/yr escalator · 20-year term |
| Tax Credit | ITC — Section 48E · 30% · PWA compliant |

---

## Key Outputs

| Metric | Value | Benchmark |
|---|---|---|
| **Project IRR** | **7.75%** | Above 7.0% debt cost — leverage accretive |
| **Equity IRR** | **9.52%** | 2.5% gap to 12% target |
| Project NPV (at 7%) | +$8.2M | Positive |
| Equity Multiple | 3.33x | — |
| Equity Payback | 11 years | — |
| Min DSCR | 1.30x | Covenant floor: 1.30x |
| Avg DSCR | 1.44x | — |
| System LCOE | $69.84/MWh | Lazard range: $50–$131 ✓ |
| Min Viable PPA Price | $55.42/MWh | LBNL market: $40–$65 ✓ |

**Capital Stack**

| Source | Amount | % |
|---|---|---|
| Senior Debt | $82.4M | 47.9% |
| ITC Cash Proceeds | $45.5M | 26.5% |
| Equity Contribution | $44.1M | 25.6% |
| **Total Project Cost** | **$172.1M** | **100%** |

---

## Model Structure

11 tabs, each with a dedicated function. All assumptions centralized on the INPUTS tab — nothing hardcoded elsewhere.

| Tab | Contents |
|---|---|
| **Outputs** | One-page dashboard: returns, capital stack, revenue breakdown, LCOE, assumptions |
| **Inputs** | All assumptions with source citations and notes |
| **Energy** | Solar loss waterfall (degradation → availability → curtailment) + BESS dispatch |
| **Revenue** | Four streams: solar PPA, BESS arbitrage, resource adequacy, ancillary services |
| **Costs** | Capex sources & uses + 30-year Opex waterfall |
| **Debt** | DSCR-constrained debt sizing via Goal Seek · amortization · DSCR profile |
| **Tax** | ITC vs. PTC comparison (Sections 48E / 45Y) · MACRS depreciation · tax liability |
| **Cashflows** | Unlevered (Project IRR) and levered (Equity IRR) cash flow waterfalls |
| **LCOE** | Solar and system LCOE · minimum viable PPA price |
| **Sensitivities** | 10 one-way tables · 4 two-way data tables · 4 named scenarios |
| **Legend** | Complete formatting and color convention guide |

---

## Revenue Model

Four distinct streams, each with independent contract structure and escalators.

| Stream | Year 1 | % of Total | Structure |
|---|---|---|---|
| Solar Energy (PPA) | $11.2M | 63% | $50/MWh · 1%/yr · 20-yr term |
| Resource Adequacy | $3.2M | 18% | 45 MW accredited · $6/kW-month |
| BESS Arbitrage | $2.2M | 12% | $65 discharge / $25 purchase · RTE-adjusted spread |
| Ancillary Services | $1.1M | 6% | $75/kW-yr · 30% capacity allocation |
| **Total** | **$17.7M** | **100%** | |

BESS arbitrage uses an explicit RTE adjustment factor (1 ÷ 85% = 1.176) to correctly express grid charging cost per MWh delivered, with separate escalators for discharge price (1.5%/yr) and purchase price (1.0%/yr) to reflect the widening duck curve spread over time.

---

## Key Design Decisions

**DSCR-constrained debt sizing.** Debt is sized via Goal Seek to a 1.30x minimum DSCR floor rather than a fixed leverage ratio, with a 65% leverage cap as a secondary hard constraint. Final debt of $82.4M (47.9%) reflects the DSCR floor binding.

**ITC over PTC despite PTC's higher NPV.** TAX tab Block 4 shows PTC is NPV-optimal (+$5M total economic value). ITC is elected because PTC credits arrive over 10 years rather than at close, which doubles required equity from $44M to $90M and reduces Equity IRR from 9.52% to 3.69%. ITC is preferred for equity return optimization under the IRA Section 6418 transferability framework.

**Decomposed O&M structure.** Rather than a single all-in O&M rate, operating costs are modeled as separate line items (maintenance, insurance, land, asset management, CAISO fees) for auditability and independent sensitivity testing.

**Separate discharge/purchase price escalators.** BESS arbitrage revenue uses different escalation rates for discharge (1.5%/yr) and grid purchase (1.0%/yr), reflecting the view that evening peak prices will rise faster than off-peak prices as solar penetration deepens.

---

## Assumptions Summary

| Category | Key Assumptions | Source |
|---|---|---|
| Solar EPC | $0.80/Wdc | NREL ATB 2024 |
| BESS EPC | $230/kWh | NREL ATB 2024 / 2025 cost projections |
| Solar O&M | $10/kWac-yr (maintenance only) | NREL ATB 2024 |
| BESS O&M | $8/kWh-yr | NREL ATB 2024 |
| Solar degradation | 0.5%/yr | NREL PV Reliability Roadmap |
| BESS degradation | 2.5%/yr | NREL ATB 2024 |
| Curtailment | 4.0% | LBNL Utility-Scale Solar 2024 |
| BESS cycles | 1.3/day · 85% RTE | Simplified dispatch assumption |
| Debt rate | 7.0% nominal | NREL ATB 2024 Financial Cases |
| ITC rate | 30% (PWA compliant) | IRA Section 48E |
| ITC transfer price | 92.5¢/$ | Norton Rose Tax Equity Outlook 2024 |
| Discount rate | 7.0% | NREL ATB 2024 Financial Cases |

Full source citations on every assumption — see INPUTS tab.

---

## Sensitivity Analysis

**One-way sensitivities** (10 tables): PPA price, capacity factor, solar EPC, BESS EPC, interconnection cost, merchant price, interest rate, BESS cycles, RA accreditation, curtailment rate.

**Two-way data tables** (4 tables):
- Equity IRR: PPA Price × Solar EPC Cost
- Equity IRR: Capacity Factor × Merchant Price
- Equity IRR: BESS EPC × BESS Cycles/Day
- Min DSCR: PPA Price × Interest Rate

**Scenario analysis** (4 scenarios): Bear, Base, Bull, and 12% Target — with full input assumption sets and output metrics for each.

The base case generates 9.52% Equity IRR against a 12% target. The minimum viable PPA price to reach 12% is $55.42/MWh — within the LBNL reported market range of $40–$65/MWh and achievable for a storage-paired project in California.

---

## Limitations

Documented transparently — these are the right next steps for a production model.

- **No construction period.** COD assumed at Year 1; no interest during construction or development-phase cash flows modeled.
- **Simplified BESS dispatch.** Annual average approach (1.3 cycles/day) rather than hourly dispatch optimization against CAISO LMP curves.
- **No battery augmentation.** Battery degrades continuously at 2.5%/yr to 48% of nameplate by Year 30 with no capacity restoration event.
- **Flat merchant price.** Post-PPA revenue uses a conservative $35/MWh flat real assumption rather than a forward price curve.
- **IRA transferability only.** ITC monetized via direct transfer (Section 6418); traditional tax equity partnership flip not modeled.
- **Single degradation rate for BESS.** Power and capacity fade modeled at the same 2.5%/yr rate; in practice capacity degrades faster than power.

---

## Data Sources

- **NREL Annual Technology Baseline 2024** — atb.nrel.gov
- **NREL Cost Projections for Utility-Scale Battery Storage: 2025 Update** — docs.nrel.gov/docs/fy25osti/93281.pdf
- **LBNL Utility-Scale Solar 2024** — emp.lbl.gov
- **Lazard LCOE Analysis v18.0 (2025)** — lazard.com
- **FERC Energy Primer 2024** — ferc.gov
- **Norton Rose Fulbright Tax Equity Market Outlook 2024** — nortonrosefulbright.com
- **IRS Publication 946** — irs.gov
- **IRA Sections 48E and 6418** — congress.gov

---

## About

Built as part of a self-directed transition into renewable energy development, targeting developer analyst roles at independent power producers and utility-scale developers. Study program covered CAISO market structure, IRA policy mechanics, interconnection queue dynamics, project finance fundamentals, and battery storage economics.

Companion Python project: CAISO demand forecasting pipeline (SARIMAX) — [github.com/tylerjsteffensen/energy_analysis](https://github.com/tylerjsteffensen/energy_analysis)

*Model version 1.0 · April 2026*
