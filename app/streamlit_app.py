import math
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import api_client as db
from api_client import run_etl as etl_run, remove_ticker_from_db, add_ticker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Cointegration test"))
from cointegration import run_all as coint_run_all
from conclusions import adf_conclusion, eg_conclusion, pair_conclusion

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Trading signals"))
from trading_signals import fetch_prices as ts_fetch_prices, compute_rolling_signals, signal_translation

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Backtest"))
from backtest import run_backtest, compute_all_metrics, get_split_dates

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Correlation Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Cached DB query wrappers ─────────────────────────────────────────────────
# Lists must be converted to tuples for st.cache_data hashability.


@st.cache_data(ttl=60, show_spinner=False)
def _tickers() -> list[str]:
    return db.get_tickers()


@st.cache_data(ttl=60, show_spinner=False)
def _stock_prices(tickers: tuple, start_date, end_date) -> pd.DataFrame:
    return db.get_stock_prices(list(tickers), start_date, end_date)


@st.cache_data(ttl=60, show_spinner=False)
def _corr_heatmap(tickers: tuple, period: str, end_date) -> pd.DataFrame:
    return db.get_corr_heatmap(list(tickers), period, end_date)


@st.cache_data(ttl=60, show_spinner=False)
def _rolling_corr(sym1: str, sym2: str, start_date, end_date, window: int) -> pd.Series:
    return db.get_rolling_corr(sym1, sym2, start_date, end_date, window)


@st.cache_data(ttl=60, show_spinner=False)
def _alert_for_date(end_date) -> dict | None:
    return db.get_alert_for_date(end_date)


@st.cache_data(ttl=300, show_spinner=False)
def _alerts(limit: int = 20) -> pd.DataFrame:
    return db.get_alerts(limit)


@st.cache_data(ttl=15, show_spinner=False)
def _etl_log(limit: int = 50) -> pd.DataFrame:
    return db.get_etl_log(limit)


def _clear_and_rerun():
    st.cache_data.clear()
    st.rerun()


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Stock Correlation")
    st.caption("Powered by yfinance + PostgreSQL")
    st.markdown("---")

    try:
        db_tickers = _tickers()
    except Exception as e:
        st.error(f"DB connection failed: {e}")
        st.info("Check your `.env` file and ensure PostgreSQL is running.")
        st.stop()

    if not db_tickers:
        st.warning("No data yet. Open **Manage Tickers** to run the ETL.")
        selected = []
    else:
        selected = st.multiselect(
            "Active Tickers",
            options=db_tickers,
            default=db_tickers,
            help="Filter which tickers appear in all charts.",
        )

    st.markdown("---")

    today = date.today()
    one_year_ago = today - timedelta(days=365)
    five_years_ago = today - timedelta(days=365 * 5)
    date_range = st.date_input(
        "Date Range",
        value=(one_year_ago, today),
        min_value=five_years_ago,
        max_value=today,
        help="Global date window used by all charts. Up to 5 years of data available.",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = one_year_ago, today

    st.markdown("---")
    st.caption(f"{len(db_tickers)} ticker(s) in DB")

# ─── Tabs ─────────────────────────────────────────────────────────────────────
(
    tab_corr,
    tab_coint,
    tab_signals,
    tab_test,
    tab_alerts,
    tab_manage,
) = st.tabs([
    "Correlation",
    "Cointegration",
    "Trading Signals",
    "Backtest (4yr/1yr)",
    "Regime Alerts",
    "Manage Tickers",
])

# ─── Tab 1: Correlation ───────────────────────────────────────────────────────
with tab_corr:
    sub_heat, sub_roll, sub_network = st.tabs(["Heatmap", "Rolling", "Network Graph"])

    with sub_heat:
        st.header("Correlation Heatmap")
        st.caption(
            "Pairwise Pearson correlation of daily returns, computed from DB prices. "
            "Use the sidebar date range to set the analysis window."
        )

        if len(selected) < 2:
            st.info("Select at least 2 tickers in the sidebar.")
        else:
            period = st.radio("Period", ["1m", "6m"], horizontal=True, key="heat_period",
                              help="1m = last 21 trading days, 6m = last 126")

            with st.spinner("Computing correlations..."):
                corr_mat = _corr_heatmap(tuple(sorted(selected)), period, end_date)

            if corr_mat.empty:
                st.info("Not enough price data for the selected parameters.")
            else:
                z = corr_mat.values.round(4)
                labels = list(corr_mat.columns)
                text = [[f"{v:.2f}" for v in row] for row in z]

                fig = go.Figure(go.Heatmap(
                    z=z,
                    x=labels,
                    y=labels,
                    text=text,
                    texttemplate="%{text}",
                    colorscale=[
                        [0.0, "#d73027"],
                        [0.25, "#f46d43"],
                        [0.5, "#f7f7f7"],
                        [0.75, "#74add1"],
                        [1.0, "#1a6faf"],
                    ],
                    zmin=-1,
                    zmax=1,
                    colorbar=dict(title="r", tickvals=[-1, -0.5, 0, 0.5, 1]),
                ))
                fig.update_layout(
                    title=f"{period} Correlation — ending {end_date}",
                    height=480,
                    xaxis=dict(side="bottom"),
                    margin=dict(l=10, r=10, t=50, b=10),
                )
                st.plotly_chart(fig, use_container_width=True)

                pairs = []
                for i in range(len(labels)):
                    for j in range(i + 1, len(labels)):
                        pairs.append({"Pair": f"{labels[i]} / {labels[j]}", "r": round(z[i][j], 4)})
                if pairs:
                    pairs_df = pd.DataFrame(pairs).sort_values("r", ascending=False, key=abs)
                    st.subheader("Ranked Pairs")
                    st.dataframe(pairs_df, use_container_width=True, hide_index=True)

    with sub_roll:
        st.header("Rolling Correlation")
        st.caption(
            "How the relationship between a pair evolves over time. "
            "Each point is the Pearson r over the trailing window. "
            "Dips toward 0 or sign flips often coincide with regime changes or idiosyncratic events."
        )

        if len(selected) < 2:
            st.info("Select at least 2 tickers in the sidebar.")
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                rc_default_a = "MSFT" if "MSFT" in selected else selected[0]
                rc_sym1 = st.selectbox("Ticker 1", selected, index=selected.index(rc_default_a), key="rc_sym1")
            with c2:
                other = [t for t in selected if t != rc_sym1]
                rc_default_b = "META" if "META" in other else other[0]
                rc_sym2 = st.selectbox("Ticker 2", other or selected, index=(other or selected).index(rc_default_b) if rc_default_b in (other or selected) else 0, key="rc_sym2")
            with c3:
                window_label = st.selectbox(
                    "Window", ["1m (21d)", "2m (42d)", "3m (63d)", "6m (126d)"], key="rc_window"
                )
            window_days = {"1m (21d)": 21, "2m (42d)": 42, "3m (63d)": 63, "6m (126d)": 126}[window_label]

            with st.spinner("Loading rolling correlation..."):
                roll = _rolling_corr(rc_sym1, rc_sym2, start_date, end_date, window_days)

            valid = roll.dropna()
            if valid.empty:
                st.info("Not enough overlapping data for this pair and window.")
            else:
                fig = go.Figure()
                fig.add_hrect(
                    y0=-0.3, y1=0.3,
                    fillcolor="rgba(180,180,180,0.15)", line_width=0,
                    annotation_text="Weak zone (|r| < 0.3)", annotation_position="top right",
                )
                fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
                fig.add_trace(go.Scatter(
                    x=roll.index, y=roll.values,
                    mode="lines",
                    name=f"{rc_sym1} / {rc_sym2}",
                    line=dict(width=2, color="#2196F3"),
                    fill="tozeroy",
                    fillcolor="rgba(33,150,243,0.10)",
                ))
                fig.update_layout(
                    title=f"Rolling {window_label} Correlation: {rc_sym1} vs {rc_sym2}  ({start_date} → {end_date})",
                    yaxis=dict(title="Pearson r", range=[-1.05, 1.05]),
                    xaxis=dict(title="Date"),
                    height=420,
                    hovermode="x unified",
                    margin=dict(l=10, r=10, t=50, b=10),
                )
                st.plotly_chart(fig, use_container_width=True)

                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("Latest r", f"{valid.iloc[-1]:.3f}")
                col_b.metric("Mean r", f"{valid.mean():.3f}")
                col_c.metric("Min r", f"{valid.min():.3f}")
                col_d.metric("Max r", f"{valid.max():.3f}")

    with sub_network:
        st.header("Correlation Network")
        st.caption(
            "Each node is a ticker. Edge thickness and color encode correlation strength — "
            "green = positive, red = negative. Use the threshold slider to reduce noise."
        )

        if len(selected) < 2:
            st.info("Select at least 2 tickers in the sidebar.")
        else:
            nc1, nc2 = st.columns([1, 3])
            with nc1:
                net_period = st.radio("Period", ["1m", "6m"], key="net_period")
                threshold = st.slider("Min |r| to show edge", 0.0, 1.0, 0.2, 0.05)

            with st.spinner("Building network..."):
                net_mat = _corr_heatmap(tuple(sorted(selected)), net_period, end_date)

            if net_mat.empty:
                st.info("No correlation data available.")
            else:
                net_tickers = list(net_mat.columns)
                n = len(net_tickers)
                angles = [2 * math.pi * i / n for i in range(n)]
                pos = {t: (math.cos(a), math.sin(a)) for t, a in zip(net_tickers, angles)}

                fig = go.Figure()

                for i in range(n):
                    for j in range(i + 1, n):
                        r = float(net_mat.iloc[i, j])
                        if abs(r) < threshold:
                            continue
                        x0, y0 = pos[net_tickers[i]]
                        x1, y1 = pos[net_tickers[j]]
                        color = f"rgba(30,120,30,{min(abs(r), 1) * 0.75})" if r > 0 else f"rgba(200,40,40,{min(abs(r), 1) * 0.75})"
                        fig.add_trace(go.Scatter(
                            x=[x0, x1, None], y=[y0, y1, None],
                            mode="lines",
                            line=dict(width=abs(r) * 10, color=color),
                            hoverinfo="skip",
                            showlegend=False,
                        ))

                fig.add_trace(go.Scatter(
                    x=[pos[t][0] for t in net_tickers],
                    y=[pos[t][1] for t in net_tickers],
                    mode="markers+text",
                    text=net_tickers,
                    textposition="top center",
                    textfont=dict(size=13, color="black"),
                    marker=dict(size=32, color="#1565C0", line=dict(width=2, color="white")),
                    hoverinfo="text",
                    showlegend=False,
                ))

                fig.update_layout(
                    title=f"Correlation Network  ({net_period}, |r| ≥ {threshold})",
                    xaxis=dict(visible=False, range=[-1.4, 1.4]),
                    yaxis=dict(visible=False, range=[-1.4, 1.4]),
                    height=520,
                    paper_bgcolor="white",
                    plot_bgcolor="white",
                    margin=dict(t=50, b=10, l=10, r=10),
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Green edge = positive correlation  |  Red edge = negative  |  Thickness ∝ |r|")

# ─── Tab 3: Cointegration Test ───────────────────────────────────────────────
with tab_coint:
    st.header("Cointegration Test")
    st.caption(
        "Tests whether two non-stationary price series share a stable long-run relationship. "
        "Uses 5-year daily adj_close prices from the DB."
    )

    db_tickers_coint = _tickers()
    default_a = "MSFT" if "MSFT" in db_tickers_coint else db_tickers_coint[0]
    default_b = "META" if "META" in db_tickers_coint else db_tickers_coint[1]

    ca1, ca2 = st.columns(2)
    with ca1:
        coint_sym_a = st.selectbox("Stock A", db_tickers_coint, index=db_tickers_coint.index(default_a), key="coint_a")
    with ca2:
        other_tickers = [t for t in db_tickers_coint if t != coint_sym_a]
        default_b_idx = other_tickers.index(default_b) if default_b in other_tickers else 0
        coint_sym_b = st.selectbox("Stock B", other_tickers, index=default_b_idx, key="coint_b")

    run_coint = st.button("Run Cointegration Test", type="primary")

    if run_coint:
        with st.spinner(f"Running tests for {coint_sym_a} / {coint_sym_b}…"):
            try:
                cr = coint_run_all(coint_sym_a, coint_sym_b)
            except Exception as exc:
                st.error(f"Test failed: {exc}")
                cr = None

        if cr:
            st.markdown("---")

            # ── Section 1: Individual ADF Tests ──────────────────────────────
            st.subheader("Step 1 — ADF Test on Individual Price Series")
            st.caption("Is each series non-stationary? (p > 0.05 → non-stationary ✓ → required for cointegration)")

            for adf_res in [cr["adf_a"], cr["adf_b"]]:
                with st.container(border=True):
                    st.markdown(f"**ADF Test: {adf_res['label']}**")
                    am1, am2, am3, am4, am5 = st.columns(5)
                    am1.metric("Test Statistic", f"{adf_res['stat']:.4f}")
                    am2.metric("P-Value", f"{adf_res['p_value']:.4f}")
                    am3.metric("Crit 1%", f"{adf_res['critical_values']['1%']:.4f}")
                    am4.metric("Crit 5%", f"{adf_res['critical_values']['5%']:.4f}")
                    am5.metric("Crit 10%", f"{adf_res['critical_values']['10%']:.4f}")
                    verdict = adf_res["verdict"]
                    conclusion = adf_conclusion(adf_res["is_stationary"])
                    if adf_res["is_stationary"]:
                        st.warning(f"{verdict} {conclusion}")
                    else:
                        st.success(f"{verdict} {conclusion}")

            st.markdown("---")

            # ── Section 2: Engle-Granger (both directions) ───────────────────
            st.subheader("Step 2 — Engle-Granger Test (both directions)")
            st.caption(
                "EG is not symmetric: regressing A on B vs B on A can produce different residuals "
                "and flip the verdict. Both directions are shown. The primary direction (lower p-value) "
                "drives the final verdict."
            )

            for eg_res, eg_dir, is_primary in [
                (cr["eg"],         cr["eg_direction"],         True),
                (cr["eg_reverse"], cr["eg_reverse_direction"], False),
            ]:
                dep, indep = eg_dir.split("→")
                badge = "**★ Primary direction**" if is_primary else "**Reverse direction**"
                line_color = "#2196F3" if is_primary else "#9C27B0"

                with st.container(border=True):
                    st.markdown(f"{badge} — `{dep}` regressed on `{indep}`")

                    bm1, bm2 = st.columns(2)
                    bm1.metric("Intercept α", f"{eg_res['alpha']:.4f}",
                               help=f"ϵt = {dep} − (α + β·{indep})")
                    bm2.metric("Hedge Ratio β", f"{eg_res['beta']:.4f}",
                               help=f"1 unit of {dep} ≈ {eg_res['beta']:.4f} units of {indep}")

                    spread = eg_res["residuals"]
                    spread_mean = spread.mean()
                    spread_std = spread.std()
                    fig_s = go.Figure()
                    fig_s.add_trace(go.Scatter(
                        x=spread.index, y=spread.values,
                        mode="lines", name="Spread", line=dict(color=line_color, width=1.5)
                    ))
                    fig_s.add_hline(y=spread_mean, line=dict(color="gray", dash="dash"), annotation_text="Mean")
                    fig_s.add_hline(y=spread_mean + spread_std, line=dict(color="#e53935", dash="dot", width=1), annotation_text="+1σ")
                    fig_s.add_hline(y=spread_mean - spread_std, line=dict(color="#e53935", dash="dot", width=1), annotation_text="-1σ")
                    fig_s.update_layout(
                        title=f"Spread: {eg_dir}",
                        xaxis_title="Date", yaxis_title="Spread",
                        height=300, margin=dict(t=50),
                    )
                    st.plotly_chart(fig_s, use_container_width=True)

                    em1, em2, em3, em4, em5 = st.columns(5)
                    em1.metric("Test Statistic", f"{eg_res['stat']:.4f}")
                    em2.metric("P-Value", f"{eg_res['p_value']:.4f}")
                    em3.metric("Crit 1%", f"{eg_res['critical_values']['1%']:.4f}")
                    em4.metric("Crit 5%", f"{eg_res['critical_values']['5%']:.4f}")
                    em5.metric("Crit 10%", f"{eg_res['critical_values']['10%']:.4f}")
                    eg_conc = eg_conclusion(eg_res["is_cointegrated"])
                    if eg_res["is_cointegrated"]:
                        st.success(f"{eg_res['verdict']} {eg_conc}")
                    else:
                        st.error(f"{eg_res['verdict']} {eg_conc}")

            st.markdown("---")

            # ── Section 3: Final Verdict ──────────────────────────────────────
            st.subheader("Final Verdict")
            st.caption(f"Based on primary direction: `{cr['eg_direction']}` (p={cr['eg']['p_value']:.4f})")
            criteria = [
                (f"ADF on {coint_sym_a}: p > 0.05 (non-stationary)", not cr["adf_a"]["is_stationary"]),
                (f"ADF on {coint_sym_b}: p > 0.05 (non-stationary)", not cr["adf_b"]["is_stationary"]),
                (f"Engle-Granger [{cr['eg_direction']}]: p < 0.05 (stationary spread)", cr["eg"]["is_cointegrated"]),
            ]
            for label, passed in criteria:
                icon = "✓" if passed else "✗"
                color = "green" if passed else "red"
                st.markdown(f":{color}[{icon}] {label}")

            pair_conc = pair_conclusion(cr["pair_passes"])
            if cr["pair_passes"]:
                st.success(f"✓ {pair_conc}")
            else:
                st.error(f"✗ {pair_conc}")

# ─── Tab 4: Trading Signals ───────────────────────────────────────────────────
with tab_signals:
    st.header("Trading Signals — Rolling Pairs Strategy")
    st.caption(
        "Rolling 90-day hedge ratio + z-score strategy. "
        "Best applied to pairs that pass the Cointegration Test. "
        "Signals: z < −2 → LONG spread, z > 2 → SHORT spread, |z| < 0.5 → EXIT."
    )

    ts_tickers = _tickers()
    ts_default_a = "MSFT" if "MSFT" in ts_tickers else ts_tickers[0]
    ts_others = [t for t in ts_tickers if t != ts_default_a]
    ts_default_b = "META" if "META" in ts_others else ts_others[0]

    tsc1, tsc2, tsc3 = st.columns([2, 2, 1])
    with tsc1:
        ts_sym_a = st.selectbox("Stock A", ts_tickers, index=ts_tickers.index(ts_default_a), key="ts_a")
    with tsc2:
        ts_b_opts = [t for t in ts_tickers if t != ts_sym_a]
        ts_sym_b = st.selectbox("Stock B", ts_b_opts,
                                index=ts_b_opts.index(ts_default_b) if ts_default_b in ts_b_opts else 0,
                                key="ts_b")
    with tsc3:
        ts_window = st.number_input("Window (days)", min_value=30, max_value=252, value=90, step=10, key="ts_win")

    ts_run = st.button("Compute Signals", type="primary", key="ts_run")

    if ts_run:
        with st.spinner(f"Computing rolling signals for {ts_sym_a} / {ts_sym_b}…"):
            try:
                ts_pa, ts_pb = ts_fetch_prices(ts_sym_a, ts_sym_b)
                ts_df = compute_rolling_signals(ts_pa, ts_pb, window=int(ts_window))
                st.session_state["ts_df"] = ts_df
                st.session_state["ts_sym_a"] = ts_sym_a
                st.session_state["ts_sym_b"] = ts_sym_b
            except Exception as exc:
                st.error(f"Computation failed: {exc}")
                st.session_state.pop("ts_df", None)

    if "ts_df" in st.session_state:
        ts_df = st.session_state["ts_df"]
        sym_a_lbl = st.session_state["ts_sym_a"]
        sym_b_lbl = st.session_state["ts_sym_b"]
        valid = ts_df.dropna(subset=["z_score"])

        st.markdown("---")

        # ── Current signal ─────────────────────────────────────────────────
        latest = valid.iloc[-1]
        cur_sig = latest["signal"]
        cur_z = latest["z_score"]
        cur_beta = latest["beta"]
        translation = signal_translation(latest, sym_a_lbl, sym_b_lbl)

        sig_color = {"LONG": "green", "SHORT": "red", "EXIT": "orange", "HOLD": "blue"}.get(cur_sig, "gray")
        st.subheader("Current Signal")
        cs1, cs2, cs3, cs4 = st.columns(4)
        cs1.metric("Signal", cur_sig)
        cs2.metric("Z-Score", f"{cur_z:.3f}")
        cs3.metric(f"β ({sym_a_lbl}/{sym_b_lbl})", f"{cur_beta:.4f}")
        cs4.metric("Position A", f"{latest['position_a']:+.0f} unit")
        st.markdown(f"**Trade:** :{sig_color}[{translation}]")
        st.caption(f"Position B size = {abs(latest['position_b']):.4f} units of {sym_b_lbl} "
                   f"(updated daily: position_B = β_t × |position_A|)")

        st.markdown("---")

        # ── Z-score chart ───────────────────────────────────────────────────
        st.subheader("Z-Score & Signals")
        sig_colors_map = {"LONG": "#1565C0", "SHORT": "#B71C1C", "EXIT": "#E65100", "HOLD": "#616161"}
        point_colors = valid["signal"].map(sig_colors_map).fillna("#616161")

        fig_z = go.Figure()
        fig_z.add_trace(go.Scatter(
            x=valid.index, y=valid["z_score"],
            mode="lines", name="Z-Score",
            line=dict(color="#78909C", width=1.2),
        ))
        # Overlay colored markers by signal
        for sig, color in sig_colors_map.items():
            mask = valid["signal"] == sig
            if mask.any():
                fig_z.add_trace(go.Scatter(
                    x=valid.index[mask], y=valid["z_score"][mask],
                    mode="markers", name=sig,
                    marker=dict(color=color, size=4),
                ))
        for level, label, dash in [(2, "+2 (SHORT)", "dash"), (-2, "−2 (LONG)", "dash"),
                                    (0.5, "+0.5 (EXIT)", "dot"), (-0.5, "−0.5 (EXIT)", "dot")]:
            fig_z.add_hline(y=level, line=dict(color="#aaa", dash=dash, width=1),
                            annotation_text=label, annotation_position="right")
        fig_z.update_layout(height=380, hovermode="x unified", margin=dict(t=30),
                            legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig_z, use_container_width=True)

        # ── Rolling β chart ─────────────────────────────────────────────────
        st.subheader("Rolling Hedge Ratio β")
        fig_b = go.Figure()
        fig_b.add_trace(go.Scatter(
            x=valid.index, y=valid["beta"],
            mode="lines", name="β_t",
            line=dict(color="#7B1FA2", width=1.5),
        ))
        fig_b.update_layout(height=260, margin=dict(t=10), yaxis_title="β",
                            hovermode="x unified")
        st.plotly_chart(fig_b, use_container_width=True)

        # ── Recent signals table ────────────────────────────────────────────
        st.subheader("Recent Signal Log")
        recent = valid.tail(30).copy()
        recent["translation"] = recent.apply(lambda r: signal_translation(r, sym_a_lbl, sym_b_lbl), axis=1)
        display_cols = ["z_score", "signal", "beta", "position_a", "position_b", "translation"]
        st.dataframe(
            recent[display_cols].rename(columns={
                "z_score": "Z-Score", "signal": "Signal", "beta": "β",
                "position_a": f"Pos {sym_a_lbl}", "position_b": f"Pos {sym_b_lbl}",
                "translation": "Trade Instruction",
            }).iloc[::-1],
            use_container_width=True, hide_index=False,
        )

        st.markdown("---")

        # ── Hypothetical PnL — Full 5-Year History ───────────────────────────
        st.subheader("Hypothetical PnL — Full 5-Year History")
        st.caption(
            "Simulated gains from following these signals over the full 5-year price history. "
            "Position sizing: ±1 unit of Stock A + β-weighted hedge in Stock B."
        )
        pnl_valid = ts_df.dropna(subset=["pnl"])

        total_pnl = pnl_valid["pnl"].sum()
        trading_days = (pnl_valid["position_a"].shift(1) != 0).sum()
        active_pnl = pnl_valid.loc[pnl_valid["position_a"].shift(1) != 0, "pnl"]
        win_rate_pnl = (active_pnl > 0).mean() * 100 if len(active_pnl) > 0 else 0
        daily_ret = pnl_valid["pnl"]
        sharpe_pnl = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
        cum = pnl_valid["cumulative_pnl"]
        max_dd = (cum - cum.cummax()).min()

        pm1, pm2, pm3, pm4, pm5 = st.columns(5)
        pm1.metric("Total PnL ($)", f"{total_pnl:+.2f}")
        pm2.metric("Sharpe Ratio", f"{sharpe_pnl:.2f}")
        pm3.metric("Max Drawdown ($)", f"{max_dd:.2f}")
        pm4.metric("Win Rate", f"{win_rate_pnl:.1f}%")
        pm5.metric("Active Days", str(int(trading_days)))

        fig_cum = go.Figure()
        fig_cum.add_trace(go.Scatter(
            x=pnl_valid.index, y=pnl_valid["cumulative_pnl"],
            mode="lines", fill="tozeroy",
            line=dict(color="#1976D2", width=1.8),
            fillcolor="rgba(25,118,210,0.12)",
            name="Cumulative PnL",
        ))
        fig_cum.add_hline(y=0, line=dict(color="#aaa", dash="dash", width=1))
        fig_cum.update_layout(title="Cumulative PnL", height=320, margin=dict(t=40),
                              yaxis_title="PnL ($)", hovermode="x unified")
        st.plotly_chart(fig_cum, use_container_width=True)

        bar_colors = np.where(pnl_valid["pnl"] >= 0, "#388E3C", "#D32F2F")
        fig_daily = go.Figure()
        fig_daily.add_trace(go.Bar(
            x=pnl_valid.index, y=pnl_valid["pnl"],
            marker_color=bar_colors, name="Daily PnL",
        ))
        fig_daily.add_hline(y=0, line=dict(color="#aaa", width=1))
        fig_daily.update_layout(title="Daily PnL", height=300, margin=dict(t=40),
                                yaxis_title="PnL ($)", hovermode="x unified")
        st.plotly_chart(fig_daily, use_container_width=True)

        monthly = pnl_valid["pnl"].resample("ME").sum().reset_index()
        monthly.columns = ["Month", "PnL"]
        monthly["Month"] = monthly["Month"].dt.strftime("%Y-%m")
        fig_m = go.Figure(go.Bar(
            x=monthly["Month"], y=monthly["PnL"],
            marker_color=np.where(monthly["PnL"] >= 0, "#388E3C", "#D32F2F"),
        ))
        fig_m.update_layout(title="Monthly PnL Breakdown", height=280, margin=dict(t=40),
                            xaxis_title="Month", yaxis_title="PnL ($)")
        st.plotly_chart(fig_m, use_container_width=True)

# ─── Tab 5: Backtest ──────────────────────────────────────────────────────────
with tab_test:
    st.header("Strategy Backtest")
    train_start, train_end, test_start, test_end = get_split_dates()
    st.caption(
        f"**Train:** {train_start} → {train_end} (4 years, used to warm up rolling models)  |  "
        f"**Test:** {test_start} → {test_end} (most recent 1 year, evaluation only). "
        "No DB writes. Results are fully in-memory."
    )

    bt_tickers = _tickers()
    bt_default_a = "MSFT" if "MSFT" in bt_tickers else bt_tickers[0]
    bt_others = [t for t in bt_tickers if t != bt_default_a]
    bt_default_b = "META" if "META" in bt_others else bt_others[0]

    btc1, btc2, btc3 = st.columns([2, 2, 1])
    with btc1:
        bt_sym_a = st.selectbox("Stock A", bt_tickers, index=bt_tickers.index(bt_default_a), key="bt_a")
    with btc2:
        bt_b_opts = [t for t in bt_tickers if t != bt_sym_a]
        bt_sym_b = st.selectbox("Stock B", bt_b_opts,
                                index=bt_b_opts.index(bt_default_b) if bt_default_b in bt_b_opts else 0,
                                key="bt_b")
    with btc3:
        bt_window = st.number_input("Window (days)", min_value=30, max_value=252, value=90, step=10, key="bt_win")

    if st.button("Run Backtest", type="primary", key="bt_run"):
        with st.spinner(f"Running backtest for {bt_sym_a} / {bt_sym_b}…"):
            try:
                _, bt_test_df = run_backtest(bt_sym_a, bt_sym_b, window=int(bt_window))
                st.session_state["bt_result"] = compute_all_metrics(bt_test_df)
                st.session_state["bt_sym_a"] = bt_sym_a
                st.session_state["bt_sym_b"] = bt_sym_b
            except Exception as exc:
                st.error(f"Backtest failed: {exc}")
                st.session_state.pop("bt_result", None)

    if "bt_result" in st.session_state:
        m = st.session_state["bt_result"]
        bta = st.session_state["bt_sym_a"]
        btb = st.session_state["bt_sym_b"]

        st.markdown("---")

        # ── Section 1: Performance ────────────────────────────────────────────
        st.subheader("1 — Performance")

        p1, p2, p3, p4, p5, p6 = st.columns(6)
        p1.metric("Total PnL ($)", f"{m['total_pnl']:+.2f}")
        p2.metric("Ann. Return", f"{m['ann_return_pct']:+.2f}%",
                  help=f"Capital proxy: mean({bta}) price = ${m['capital_proxy']:.0f}")
        p3.metric("Sharpe", f"{m['sharpe']:.3f}", delta=m["sharpe_label"],
                  delta_color="off")
        p4.metric("Max Drawdown ($)", f"{m['max_drawdown']:.2f}")
        p5.metric("Calmar", f"{m['calmar']}" if m["calmar"] else "N/A",
                  delta=m["calmar_label"], delta_color="off")
        p6.metric("Win Rate", f"{m['win_rate']:.1f}%")

        pp1, pp2, pp3 = st.columns(3)
        pp1.metric("Avg Profit / Trade ($)", f"{m['avg_profit_per_trade']:.2f}" if m["avg_profit_per_trade"] is not None else "N/A")
        pp2.metric("5th Pct Trade PnL ($)", f"{m['pct5_trade_pnl']:.2f}" if m["pct5_trade_pnl"] is not None else "N/A",
                   help="Worst 5% of trades")
        pp3.metric("95th Pct Trade PnL ($)", f"{m['pct95_trade_pnl']:.2f}" if m["pct95_trade_pnl"] is not None else "N/A",
                   help="Best 5% of trades")

        st.markdown("**Quarterly Sharpe**")
        if m["quarterly_sharpe"]:
            qs_df = pd.DataFrame(list(m["quarterly_sharpe"].items()), columns=["Quarter", "Sharpe"])
            qs_df["Rating"] = qs_df["Sharpe"].apply(
                lambda s: "Strong" if s > 2 else ("Decent" if s > 1 else ("Weak" if s > 0.5 else "Bad"))
            )
            st.dataframe(qs_df, use_container_width=False, hide_index=True)

        # Rolling Sharpe chart
        rs30 = m["rolling_sharpe_30"].dropna()
        rs60 = m["rolling_sharpe_60"].dropna()
        fig_rs = go.Figure()
        fig_rs.add_trace(go.Scatter(x=rs30.index, y=rs30.values, mode="lines",
                                    name="30-day Sharpe", line=dict(color="#1976D2", width=1.5)))
        fig_rs.add_trace(go.Scatter(x=rs60.index, y=rs60.values, mode="lines",
                                    name="60-day Sharpe", line=dict(color="#F57C00", width=1.5)))
        fig_rs.add_hline(y=0, line=dict(color="#aaa", dash="dash", width=1))
        fig_rs.update_layout(title="Rolling Sharpe Ratio", height=280, margin=dict(t=40),
                              hovermode="x unified", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig_rs, use_container_width=True)

        # Cumulative PnL + Drawdown
        fig_perf = go.Figure()
        fig_perf.add_trace(go.Scatter(x=m["cum_pnl"].index, y=m["cum_pnl"].values,
                                      mode="lines", name="Cumulative PnL",
                                      line=dict(color="#1976D2", width=1.8),
                                      fill="tozeroy", fillcolor="rgba(25,118,210,0.10)"))
        fig_perf.add_trace(go.Scatter(x=m["drawdown"].index, y=m["drawdown"].values,
                                      mode="lines", name="Drawdown",
                                      line=dict(color="#D32F2F", width=1.2),
                                      fill="tozeroy", fillcolor="rgba(211,47,47,0.08)"))
        fig_perf.add_hline(y=0, line=dict(color="#aaa", dash="dash", width=1))
        fig_perf.update_layout(title="Cumulative PnL & Drawdown (test period)",
                               height=320, margin=dict(t=40),
                               hovermode="x unified", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig_perf, use_container_width=True)

        st.markdown("---")

        # ── Section 2: Trading Activity ───────────────────────────────────────
        st.subheader("2 — Trading Activity")

        ta1, ta2, ta3, ta4 = st.columns(4)
        ta1.metric("Total Trades", m["n_trades"])
        ta2.metric("Avg Holding Period", f"{m['avg_holding']} days" if m["avg_holding"] else "N/A")
        ta3.metric("Half-life", f"{m['halflife']} days" if m["halflife"] else "N/A",
                   delta=m["halflife_label"], delta_color="off")
        ta4.metric("Total Turnover ($)", f"{m['total_turnover']:,.0f}")

        # Transaction cost sensitivity table
        st.markdown("**Turnover / Transaction Cost Sensitivity**")
        cost_df = pd.DataFrame([
            {"Cost (bps)": bps, "Sharpe": shp,
             "Interpretation": (
                 "Theoretical edge (no cost)" if bps == 0 else
                 "Realistic equities" if bps <= 5 else
                 "Stress test"
             )}
            for bps, shp in m["cost_scenarios"].items()
        ])
        st.dataframe(cost_df, use_container_width=False, hide_index=True)
        if "Good" in m["cost_label"]:
            st.success(f"✓ {m['cost_label']}")
        elif "Bad" in m["cost_label"]:
            st.error(f"✗ {m['cost_label']}")
        else:
            st.warning(f"~ {m['cost_label']}")

        if m["n_trades"] > 0:
            st.markdown("**Trade Log**")
            tlog = m["trades_df"].copy()
            tlog["entry_date"] = tlog["entry_date"].dt.strftime("%Y-%m-%d")
            tlog["exit_date"] = tlog["exit_date"].dt.strftime("%Y-%m-%d")
            tlog["pnl"] = tlog["pnl"].round(2)
            st.dataframe(tlog.rename(columns={
                "entry_date": "Entry", "exit_date": "Exit",
                "direction": "Direction", "holding_days": "Holding Days", "pnl": "PnL ($)"
            }), use_container_width=True, hide_index=True)

        st.markdown("---")

        # ── Section 3: Risk Metrics ───────────────────────────────────────────
        st.subheader("3 — Risk Metrics")

        rm1, rm2, rm3, rm4, rm5 = st.columns(5)
        rm1.metric("Ann. Volatility ($)", f"{m['vol_ann']:.2f}")
        rm2.metric("Skewness", f"{m['skewness']:.3f}",
                   help="Negative = left tail risk; positive = right tail upside")
        rm3.metric("Kurtosis", f"{m['kurtosis']:.3f}",
                   help="Excess kurtosis (0 = normal). >3 = fat tails")
        rm4.metric("VaR 95% (daily $)", f"{m['var_95']:.2f}",
                   help="5th percentile of daily PnL — worst day in 20")
        rm5.metric("CVaR 95% (daily $)", f"{m['cvar_95']:.2f}",
                   help="Expected loss on days beyond the 95% VaR threshold")

        rl1, rl2 = st.columns(2)
        rl1.metric("Max Losing Streak", f"{m['max_losing_streak_days']} days")
        rl2.metric("Max Losing Streak Value ($)", f"{m['max_losing_streak_val']:.2f}")

        st.markdown("---")

        # ── Section 4: Stability ──────────────────────────────────────────────
        st.subheader("4 — Stability")
        st.caption("All series computed over the test period using rolling 60-day windows.")

        stab1, stab2 = st.columns(2)

        with stab1:
            # Rolling ADF p-value on spread
            radf = m["rolling_adf"].dropna()
            fig_adf = go.Figure()
            fig_adf.add_trace(go.Scatter(x=radf.index, y=radf.values, mode="lines",
                                         name="ADF p-value", line=dict(color="#7B1FA2", width=1.5)))
            fig_adf.add_hline(y=0.05, line=dict(color="#e53935", dash="dash", width=1),
                              annotation_text="p=0.05 threshold")
            fig_adf.update_layout(title="Spread Stationarity (Rolling ADF p-value)",
                                  height=260, margin=dict(t=40), yaxis_title="p-value")
            st.plotly_chart(fig_adf, use_container_width=True)
            radf_std = float(radf.std())
            st.caption(f"Std dev of rolling ADF p-value: {radf_std:.4f}")

        with stab2:
            # Z-score distribution
            zvals = m["zscore_vals"]
            fig_z = go.Figure()
            fig_z.add_trace(go.Histogram(x=zvals.values, nbinsx=40,
                                         marker_color="#1976D2", opacity=0.75, name="Z-score"))
            fig_z.add_vline(x=2, line=dict(color="#e53935", dash="dash", width=1), annotation_text="SHORT")
            fig_z.add_vline(x=-2, line=dict(color="#1565C0", dash="dash", width=1), annotation_text="LONG")
            fig_z.update_layout(title="Z-Score Distribution", height=260,
                                margin=dict(t=40), xaxis_title="Z-Score", yaxis_title="Count")
            st.plotly_chart(fig_z, use_container_width=True)

        stab3, stab4 = st.columns(2)

        with stab3:
            # Hedge ratio β
            beta_s = m["beta_series"]
            fig_beta = go.Figure()
            fig_beta.add_trace(go.Scatter(x=beta_s.index, y=beta_s.values, mode="lines",
                                          name="β", line=dict(color="#F57C00", width=1.5)))
            fig_beta.update_layout(title="Hedge Ratio β (test period)", height=260,
                                   margin=dict(t=40), yaxis_title="β")
            st.plotly_chart(fig_beta, use_container_width=True)
            st.caption(f"Std dev of β: {float(beta_s.std()):.4f}")

        with stab4:
            # Rolling half-life
            rhl = m["rolling_halflife"].dropna()
            fig_hl = go.Figure()
            fig_hl.add_trace(go.Scatter(x=rhl.index, y=rhl.values, mode="lines",
                                         name="Half-life (days)", line=dict(color="#388E3C", width=1.5)))
            fig_hl.add_hline(y=20, line=dict(color="#e53935", dash="dot", width=1), annotation_text="20d upper ideal")
            fig_hl.add_hline(y=5,  line=dict(color="#1565C0", dash="dot", width=1), annotation_text="5d lower ideal")
            fig_hl.update_layout(title="Rolling Half-Life (60-day window)", height=260,
                                 margin=dict(t=40), yaxis_title="Days")
            st.plotly_chart(fig_hl, use_container_width=True)
            rhl_std = float(rhl.std()) if len(rhl) > 1 else 0.0
            st.caption(f"Std dev of rolling half-life: {rhl_std:.2f} days")

        if m["trade_pnl_std"] is not None:
            st.metric("Std Dev of Trade PnL ($)", f"{m['trade_pnl_std']:.2f}",
                      help="Dispersion of individual trade outcomes")

        st.markdown("---")

        # ── Section 5: Scalability ────────────────────────────────────────────
        st.subheader("5 — Scalability")
        st.caption(
            "Simulates scaling position size to 2× and 5× of current. "
            "Sharpe is scale-invariant (mean/std ratio unchanged). "
            "Differences in absolute PnL and drawdown are highlighted below."
        )

        sr = m["scale_results"]
        base = sr[1]

        def _diff(new, old, fmt=".2f"):
            d = new - old
            sign = "+" if d >= 0 else ""
            return f"{sign}{d:{fmt}}"

        scale_lines = []
        for scale, label in [(2, "PnL ×2"), (5, "PnL ×5")]:
            s = sr[scale]
            sharpe_note = "unchanged (scale-invariant)" if abs(s["sharpe"] - base["sharpe"]) < 0.01 else f"changed to {s['sharpe']}"
            scale_lines.append(
                f"**{label}:** Total PnL ${s['total_pnl']:+.2f} ({_diff(s['total_pnl'], base['total_pnl'])} vs base)  |  "
                f"Ann. PnL ${s['ann_pnl']:+.2f}  |  "
                f"Max DD ${s['max_dd']:.2f} ({_diff(s['max_dd'], base['max_dd'])} vs base)  |  "
                f"Sharpe {s['sharpe']:.3f} ({sharpe_note})  |  "
                f"Win Rate {s['win_rate']:.1f}% (unchanged)"
            )
        for line in scale_lines:
            st.markdown(line)


# ─── Tab 6: Regime Alerts ─────────────────────────────────────────────────────
with tab_alerts:
    st.header("Regime Alerts & Commentary")
    st.caption(
        "Compares correlations at the sidebar end date against a baseline ~30 days prior. "
        "Requires ≥ 30 days of correlation history — sub-month comparisons are too noisy to interpret."
    )

    alert = _alert_for_date(end_date)

    if alert and alert.get("commentary"):
        al1, al2 = st.columns([3, 1])
        with al1:
            st.markdown(f"{alert['commentary']}")
        with al2:
            st.metric("Analysis date", str(alert["corr_date"]))
            st.metric("Baseline date", str(alert["baseline_date"]))
    else:
        st.info(
            f"No alert stored for **{end_date}**. "
            "Click below to generate one — this calls Claude and takes a few seconds."
        )
        if st.button("Generate Alert for this date", type="primary", key="gen_alert_btn"):
            with st.spinner("Analysing correlations via Claude..."):
                try:
                    result = db.generate_alert_for_date(end_date)
                    if result:
                        _clear_and_rerun()
                    else:
                        st.error(
                            f"Not enough correlation history for {end_date}. "
                            "Need a snapshot ≥ 30 days before that date in correlation_history."
                        )
                except Exception as exc:
                    st.error(f"Generation failed: {exc}")

    alerts_df = _alerts(20)
    if not alerts_df.empty:
        st.markdown("---")
        st.subheader("Alert History")
        history = alerts_df[["generated_at", "corr_date", "baseline_date", "commentary"]].copy()
        history.columns = ["Generated At", "Analysis Date", "Baseline Date", "Commentary"]
        st.dataframe(history, use_container_width=True, hide_index=True)

# ─── Tab 8: Manage Tickers ───────────────────────────────────────────────────
with tab_manage:
    st.header("Manage Tickers")

    # Current tickers
    st.subheader("Tickers currently in DB")
    if db_tickers:
        st.write("  ".join(f"`{t}`" for t in db_tickers))
    else:
        st.info("No tickers yet.")

    st.markdown("---")

    # ── Add ticker ──
    st.subheader("Add Ticker")
    st.caption(
        "Fetches 5y of price data for the new ticker from yfinance, inserts it, "
        "then recomputes correlations for all active tickers."
    )
    add_col1, add_col2 = st.columns([2, 1])
    with add_col1:
        new_ticker = st.text_input("Symbol", key="add_input").strip().upper()
    with add_col2:
        st.write("")
        st.write("")
        add_btn = st.button("Add & Run ETL", type="primary", key="add_btn")

    if add_btn:
        if not new_ticker:
            st.warning("Enter a ticker symbol.")
        elif new_ticker in db_tickers:
            st.warning(f"{new_ticker} is already in the database.")
        else:
            with st.spinner(f"Fetching {new_ticker} and recomputing correlations..."):
                try:
                    add_ticker(new_ticker)
                    st.success(f"Added {new_ticker}.")
                    _clear_and_rerun()
                except Exception as exc:
                    st.error(f"ETL failed: {exc}")

    st.markdown("---")

    # ── Remove ticker ──
    st.subheader("Remove Ticker")
    st.caption("Deletes all data for this ticker — prices, correlations, history, and company record. Other pairs are unaffected.")
    if db_tickers:
        rm_col1, rm_col2 = st.columns([2, 1])
        with rm_col1:
            to_remove = st.selectbox("Ticker to remove", [""] + db_tickers, key="rm_select")
        with rm_col2:
            st.write("")
            st.write("")
            rm_btn = st.button("Remove", type="secondary", key="rm_btn")

        if rm_btn:
            if not to_remove:
                st.warning("Select a ticker to remove.")
            else:
                with st.spinner(f"Removing {to_remove}..."):
                    try:
                        remove_ticker_from_db(to_remove)
                        st.success(f"Removed {to_remove}.")
                        _clear_and_rerun()
                    except Exception as exc:
                        st.error(f"Failed: {exc}")

    st.markdown("---")

    # ── Manual refresh ──
    st.subheader("Manual ETL Refresh")
    st.caption(
        "Re-fetches the latest 5y of prices for all DB tickers, updates stock_prices, "
        "and recomputes correlations. Safe to run repeatedly — prices are idempotent."
    )
    if st.button("Refresh All Data", type="primary", key="refresh_btn"):
        current = db.get_tickers()
        if not current:
            st.warning("No tickers in DB. Add some first.")
        else:
            with st.spinner(f"Running ETL for {', '.join(current)}..."):
                try:
                    etl_run(tickers=current or None)
                    st.success("Refresh complete.")
                    _clear_and_rerun()
                except Exception as exc:
                    st.error(f"ETL failed: {exc}")

