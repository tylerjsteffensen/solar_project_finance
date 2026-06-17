"""
Interactive Streamlit dashboard for the BESS hourly dispatch model.

Run from the repository root (the directory containing the ``bess_dispatch``
package):

    streamlit run bess_dispatch/dashboard/app.py

On first run it loads cached CAISO SP15 LMP data if present, otherwise fetches
and caches fresh data (falling back to a synthetic price series if OASIS is
unreachable). Four panels:

    1. CAISO SP15 LMP profile (heatmap, duration curve, seasonal daily shape)
    2. BESS dispatch profile (selectable week + monthly summary)
    3. Revenue comparison vs the Excel project finance model
    4. Sensitivity analysis (duration and cycles/day)

This module is import-safe: all heavy work happens inside Streamlit-cached
functions so the page reruns are fast.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# Make the repo root importable when launched via `streamlit run <path>`.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bess_dispatch import config  # noqa: E402
from bess_dispatch.analysis import comparison, metrics  # noqa: E402
from bess_dispatch.data.caiso_fetch import get_hourly_lmp  # noqa: E402
from bess_dispatch.data.solar_gen import generate_solar  # noqa: E402
from bess_dispatch.dispatch.optimizer import run_dispatch  # noqa: E402

SEASONS = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Fall", 10: "Fall", 11: "Fall",
}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------- #
# Cached data loaders
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Loading CAISO SP15 LMP data…")
def load_lmp(year: int, force_refresh: bool = False) -> pd.DataFrame:
    """Load (and cache) the hourly LMP series. Returns a frame with a 'source' col."""
    df = get_hourly_lmp(year, force_refresh=force_refresh)
    df = df.copy()
    df["source"] = df.attrs.get("source", "unknown")
    return df


@st.cache_data(show_spinner="Generating synthetic solar…")
def load_solar(year: int) -> pd.DataFrame:
    """Load (and cache) the synthetic solar series."""
    return generate_solar(year)


@st.cache_data(show_spinner="Running greedy dispatch…")
def load_dispatch(year: int, _lmp: pd.DataFrame) -> pd.DataFrame:
    """Run (and cache) the dispatch. ``_lmp`` is excluded from the cache key."""
    return run_dispatch(_lmp, year=config.PROJECT_YEAR)


@st.cache_data(show_spinner="Running duration sensitivity…")
def load_duration_sens(year: int, _lmp: pd.DataFrame) -> pd.DataFrame:
    """Run (and cache) the duration sensitivity sweep."""
    return comparison.duration_sensitivity(_lmp)


# --------------------------------------------------------------------------- #
# Panel 1 — LMP profile
# --------------------------------------------------------------------------- #
def panel_lmp(lmp: pd.DataFrame) -> None:
    """Render the CAISO SP15 LMP profile panel."""
    st.header("Panel 1 — CAISO SP15 LMP Profile")

    df = lmp.copy()
    df["hour"] = df.index.hour
    df["month"] = df.index.month
    df["season"] = df["month"].map(SEASONS)

    c1, c2 = st.columns([1.2, 1])

    with c1:
        st.subheader("Monthly average LMP heatmap (hour × month)")
        pivot = df.pivot_table(values="lmp", index="hour", columns="month", aggfunc="mean")
        fig = go.Figure(go.Heatmap(
            z=pivot.values,
            x=[MONTH_NAMES[m - 1] for m in pivot.columns],
            y=pivot.index,
            colorscale="RdYlBu_r",
            colorbar=dict(title="$/MWh"),
        ))
        fig.update_layout(xaxis_title="Month", yaxis_title="Hour of day",
                          height=420, margin=dict(t=10))
        st.plotly_chart(fig, width='stretch')

    with c2:
        st.subheader("Annual LMP duration curve")
        sorted_lmp = np.sort(df["lmp"].to_numpy())[::-1]
        pct = np.linspace(0, 100, len(sorted_lmp))
        fig = go.Figure(go.Scatter(x=pct, y=sorted_lmp, mode="lines",
                                   line=dict(color="#1f77b4")))
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(xaxis_title="% of hours exceeded",
                          yaxis_title="LMP ($/MWh)", height=420, margin=dict(t=10))
        st.plotly_chart(fig, width='stretch')

    st.subheader("Average daily LMP profile by season")
    seasonal = df.groupby(["season", "hour"])["lmp"].mean().reset_index()
    fig = go.Figure()
    for season in ["Winter", "Spring", "Summer", "Fall"]:
        s = seasonal[seasonal["season"] == season]
        fig.add_trace(go.Scatter(x=s["hour"], y=s["lmp"], mode="lines+markers",
                                 name=season))
    fig.update_layout(xaxis_title="Hour of day", yaxis_title="Avg LMP ($/MWh)",
                      height=360, margin=dict(t=10))
    st.plotly_chart(fig, width='stretch')


# --------------------------------------------------------------------------- #
# Panel 2 — Dispatch profile
# --------------------------------------------------------------------------- #
def panel_dispatch(dispatch: pd.DataFrame) -> None:
    """Render the BESS dispatch profile panel."""
    st.header("Panel 2 — BESS Dispatch Profile")

    n_weeks = int(np.ceil(len(dispatch) / (24 * 7)))
    week = st.slider("Select week of year", min_value=1, max_value=n_weeks, value=28)
    start = (week - 1) * 24 * 7
    wk = dispatch.iloc[start:start + 24 * 7]

    st.subheader(f"Week {week}: SoC, charge/discharge, and LMP")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=wk.index, y=wk["charge_mw"], name="Charge (MW)",
                         marker_color="#2ca02c", opacity=0.6), secondary_y=False)
    fig.add_trace(go.Bar(x=wk.index, y=-wk["discharge_mw"], name="Discharge (MW)",
                         marker_color="#d62728", opacity=0.6), secondary_y=False)
    fig.add_trace(go.Scatter(x=wk.index, y=wk["soc_pct"], name="SoC (%)",
                             line=dict(color="#1f77b4", width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(x=wk.index, y=wk["lmp"], name="LMP ($/MWh)",
                             line=dict(color="#ff7f0e", width=2, dash="dot")),
                  secondary_y=True)
    fig.update_yaxes(title_text="MW  /  SoC %", secondary_y=False)
    fig.update_yaxes(title_text="LMP ($/MWh)", secondary_y=True)
    fig.update_layout(barmode="relative", height=460, margin=dict(t=10),
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width='stretch')

    st.subheader("Monthly dispatch summary")
    summ = metrics.monthly_summary(dispatch)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=[MONTH_NAMES[m - 1] for m in summ.index],
                         y=summ["charge_mwh"], name="Charge (MWh)",
                         marker_color="#2ca02c"), secondary_y=False)
    fig.add_trace(go.Bar(x=[MONTH_NAMES[m - 1] for m in summ.index],
                         y=summ["discharge_mwh"], name="Discharge (MWh)",
                         marker_color="#d62728"), secondary_y=False)
    fig.add_trace(go.Scatter(x=[MONTH_NAMES[m - 1] for m in summ.index],
                             y=summ["revenue"], name="Net revenue ($)",
                             line=dict(color="#1f77b4", width=3)), secondary_y=True)
    fig.update_yaxes(title_text="Energy (MWh)", secondary_y=False)
    fig.update_yaxes(title_text="Revenue ($)", secondary_y=True)
    fig.update_layout(barmode="group", height=400, margin=dict(t=10),
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width='stretch')


# --------------------------------------------------------------------------- #
# Panel 3 — Revenue comparison
# --------------------------------------------------------------------------- #
def panel_comparison(dispatch: pd.DataFrame) -> None:
    """Render the revenue-comparison panel."""
    st.header("Panel 3 — Revenue Comparison vs Excel Model")

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("BESS revenue by component")
        comp = comparison.revenue_components(dispatch)
        fig = go.Figure()
        for col, color in [("dispatch_model", "#1f77b4"), ("excel_model", "#ff7f0e")]:
            fig.add_trace(go.Bar(name=col.replace("_", " ").title(),
                                 x=comp.index, y=comp[col], marker_color=color))
        fig.update_layout(barmode="group", yaxis_title="Annual revenue ($)",
                          height=400, margin=dict(t=10),
                          legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, width='stretch')

    with c2:
        st.subheader("Arbitrage variance waterfall")
        wf = comparison.variance_waterfall(dispatch)
        fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=wf["measure"].tolist(),
            x=wf["label"].tolist(),
            y=wf["amount"].tolist(),
            connector=dict(line=dict(color="gray")),
            decreasing=dict(marker=dict(color="#d62728")),
            increasing=dict(marker=dict(color="#2ca02c")),
            totals=dict(marker=dict(color="#1f77b4")),
        ))
        fig.update_layout(yaxis_title="$", height=400, margin=dict(t=10))
        st.plotly_chart(fig, width='stretch')

    st.subheader("Key metrics comparison")
    table = comparison.comparison_table(dispatch).copy()
    fmt = {
        "dispatch_model": "{:,.2f}", "excel_model": "{:,.2f}",
        "delta": "{:,.2f}", "delta_pct": "{:,.1f}%",
    }
    for col, f in fmt.items():
        table[col] = table[col].map(lambda v, f=f: "" if pd.isna(v) else f.format(v))
    st.dataframe(table, width='stretch', hide_index=True)


# --------------------------------------------------------------------------- #
# Panel 4 — Sensitivity
# --------------------------------------------------------------------------- #
def panel_sensitivity(dispatch: pd.DataFrame, dur_sens: pd.DataFrame) -> None:
    """Render the sensitivity-analysis panel."""
    st.header("Panel 4 — Sensitivity Analysis")

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Annual revenue vs BESS duration")
        st.caption("Greedy dispatch re-run at each duration; 50 MW power held constant.")
        fig = go.Figure(go.Scatter(
            x=dur_sens["duration_h"], y=dur_sens["annual_revenue"],
            mode="lines+markers", line=dict(color="#1f77b4", width=3),
            marker=dict(size=10)))
        fig.add_vline(x=4, line_dash="dot", line_color="gray",
                      annotation_text="Base (4h)")
        fig.update_layout(xaxis_title="Duration (hours)",
                          yaxis_title="Annual arbitrage revenue ($)",
                          height=400, margin=dict(t=10))
        st.plotly_chart(fig, width='stretch')

    with c2:
        st.subheader("Annual revenue vs cycles/day (Excel methodology)")
        st.caption("Excel treats revenue as linear in cycles/day; dispatch point shown.")
        cyc = comparison.cycles_sensitivity(dispatch)
        pt = cyc.attrs["dispatch_point"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=cyc["cycles_per_day"], y=cyc["excel_revenue"],
                                 mode="lines+markers", name="Excel (linear)",
                                 line=dict(color="#ff7f0e", width=3)))
        fig.add_trace(go.Scatter(x=[pt["cycles_per_day"]], y=[pt["annual_revenue"]],
                                 mode="markers", name="Dispatch model",
                                 marker=dict(color="#1f77b4", size=16, symbol="star")))
        fig.update_layout(xaxis_title="Cycles per day",
                          yaxis_title="Annual arbitrage revenue ($)",
                          height=400, margin=dict(t=10),
                          legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, width='stretch')


# --------------------------------------------------------------------------- #
# Main app
# --------------------------------------------------------------------------- #
def main() -> None:
    """Build and render the full dashboard."""
    st.set_page_config(page_title="BESS Dispatch — CAISO SP15", layout="wide")
    st.title("⚡ BESS Hourly Dispatch — CAISO SP15")
    st.caption("Solar-plus-storage · 100 MWac PV + 50 MW / 200 MWh BESS · "
               "Year 1, nominal USD")

    with st.sidebar:
        st.header("Controls")
        # Upper bound is the current year so the configured DATA_YEAR is always
        # selectable (OASIS only serves complete years up to last year, but the
        # synthetic fallback covers any year).
        _max_year = max(config.DATA_YEAR, pd.Timestamp.today().year)
        year = st.number_input("Data year", min_value=2019, max_value=_max_year,
                               value=config.DATA_YEAR, step=1)
        force_refresh = st.checkbox("Force re-fetch from OASIS", value=False)
        st.markdown("---")
        st.markdown(
            f"**BESS:** {config.BESS_POWER_MW:.0f} MW / "
            f"{config.BESS_ENERGY_MWH:.0f} MWh  \n"
            f"**RTE:** {config.BESS_RTE:.0%}  \n"
            f"**SoC band:** {config.SOC_MIN_FRAC:.0%}–{config.SOC_MAX_FRAC:.0%}  \n"
            f"**RA reserve:** {config.RA_CAPACITY_MW:.0f} MW (HE16–21)"
        )

    lmp = load_lmp(int(year), force_refresh)
    source = lmp["source"].iloc[0]
    if source == "synthetic":
        st.warning(
            "⚠️ Using **synthetic** SP15 prices — the CAISO OASIS API was "
            "unreachable (firewall/throttle/offline). Numbers are illustrative. "
            "Re-run with network access to OASIS for real market data.",
            icon="⚠️",
        )
    else:
        st.success(f"✅ Loaded real CAISO SP15 LMP data for {int(year)}.")

    load_solar(int(year))  # warm the solar cache (used contextually)
    dispatch = load_dispatch(int(year), lmp)
    dur_sens = load_duration_sens(int(year), lmp)

    tab1, tab2, tab3, tab4 = st.tabs([
        "1 · LMP Profile", "2 · Dispatch", "3 · Revenue", "4 · Sensitivity",
    ])
    with tab1:
        panel_lmp(lmp)
    with tab2:
        panel_dispatch(dispatch)
    with tab3:
        panel_comparison(dispatch)
    with tab4:
        panel_sensitivity(dispatch, dur_sens)


if __name__ == "__main__":
    main()
else:
    # `streamlit run` executes the module top-to-bottom without __main__.
    main()
