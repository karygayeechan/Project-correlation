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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Regime detection agent"))
from data_collector import fetch_indicators
from regime_alerts import detect_alerts

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fundamental comparison agent"))
from fundamental_data import fetch_fundamentals, INCOME_DISPLAY, BALANCE_DISPLAY, CASHFLOW_DISPLAY, DERIVED_DISPLAY
from fundamental_alerts import detect_fundamental_alerts
from fundamental_commentary import generate_fundamental_commentary

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Correlation Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Auto-refresh: run ETL once per session if data is stale ─────────────────
if "etl_auto_refreshed" not in st.session_state:
    st.session_state["etl_auto_refreshed"] = False

if not st.session_state["etl_auto_refreshed"]:
    try:
        latest = db.get_latest_price_date()
        today = date.today()
        # On weekdays, refresh if data isn't from today.
        # On weekends, Friday data is current — skip.
        is_weekday = today.weekday() < 5
        data_is_stale = latest is None or (is_weekday and latest < today)
        if data_is_stale:
            all_tickers = db.get_tickers()
            with st.spinner(f"Refreshing market data for {len(all_tickers)} tickers…"):
                etl_run(tickers=all_tickers if all_tickers else None)
            st.cache_data.clear()
    except Exception:
        pass  # Never block the dashboard if auto-refresh fails
    st.session_state["etl_auto_refreshed"] = True

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


@st.cache_data(ttl=3600, show_spinner=False)
def _regime_indicators(lookback_days: int = 365) -> pd.DataFrame:
    return fetch_indicators(lookback_days)


@st.cache_data(ttl=21600, show_spinner=False)
def _fetch_fundamentals_cached(symbol: str) -> dict:
    return fetch_fundamentals(symbol)


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
    tab_fund,
    tab_manage,
) = st.tabs([
    "Correlation",
    "Cointegration",
    "Trading Signals",
    "Backtest (4yr/1yr)",
    "Regime Alerts",
    "Fundamental Comparison",
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
            period = st.radio("Period", ["6m", "12m", "24m"], index=2, horizontal=True, key="heat_period",
                              help="6m = last 126 trading days, 12m = last 252, 24m = last 504")

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

                # Build ranked pairs table with all three periods; sort by selected period
                ticker_tuple = tuple(sorted(selected))
                with st.spinner("Loading all periods for ranked pairs..."):
                    mat_6m  = _corr_heatmap(ticker_tuple, "6m",  end_date)
                    mat_12m = _corr_heatmap(ticker_tuple, "12m", end_date)
                    mat_24m = _corr_heatmap(ticker_tuple, "24m", end_date)

                pairs = []
                for i in range(len(labels)):
                    for j in range(i + 1, len(labels)):
                        a, b = labels[i], labels[j]
                        r6  = round(float(mat_6m.loc[a, b]),  4) if not mat_6m.empty  else None
                        r12 = round(float(mat_12m.loc[a, b]), 4) if not mat_12m.empty else None
                        r24 = round(float(mat_24m.loc[a, b]), 4) if not mat_24m.empty else None
                        pairs.append({"Pair": f"{a} / {b}", "24m r": r24, "12m r": r12, "6m r": r6})

                if pairs:
                    sort_col = {"6m": "6m r", "12m": "12m r", "24m": "24m r"}[period]
                    pairs_df = pd.DataFrame(pairs).sort_values(sort_col, ascending=False, key=abs)
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
            c1, c2 = st.columns(2)
            with c1:
                rc_default_a = "ARM" if "ARM" in selected else selected[0]
                rc_sym1 = st.selectbox("Ticker 1", selected, index=selected.index(rc_default_a), key="rc_sym1")
            with c2:
                other = [t for t in selected if t != rc_sym1]
                rc_default_b = "TSM" if "TSM" in other else other[0]
                rc_sym2 = st.selectbox("Ticker 2", other or selected, index=(other or selected).index(rc_default_b) if rc_default_b in (other or selected) else 0, key="rc_sym2")
            # Always pin to today so the chart stays current regardless of sidebar date range
            window_days = 90
            rc_end = date.today()
            rc_start = rc_end - timedelta(days=1900)  # ~5.2 years of history

            with st.spinner("Loading rolling correlation..."):
                roll = _rolling_corr(rc_sym1, rc_sym2, rc_start, rc_end, window_days)

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
                    title=f"Rolling Correlation 5yr (90d window): {rc_sym1} vs {rc_sym2}  ({rc_start} → {rc_end})",
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
                net_period = st.radio("Period", ["24m", "60m"], index=0, key="net_period")
                threshold = st.slider("Min |r| to show edge", 0.0, 1.0, 0.65, 0.05)

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
                    title=f"Correlation Network  ({net_period}, |r| ≥ {threshold:.2f})",
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
        "The past year is split into 4 quarterly windows. Each quarter produces its own "
        "Engle-Granger p-value — 4 p-values in total, one per quarter."
    )

    db_tickers_coint = _tickers()
    default_a = "ARM" if "ARM" in db_tickers_coint else db_tickers_coint[0]
    default_b = "TSM" if "TSM" in db_tickers_coint else db_tickers_coint[1]

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

            # ── Section 1: ADF prerequisite banner (5-year basis) ────────────
            adf_results = [cr["adf_a"], cr["adf_b"]]
            stationary = [r for r in adf_results if r["is_stationary"]]
            non_stationary = [r for r in adf_results if not r["is_stationary"]]

            if not stationary:
                st.success(
                    f"✓ **{coint_sym_a}** (ADF p={cr['adf_a']['p_value']:.4f}) and "
                    f"**{coint_sym_b}** (ADF p={cr['adf_b']['p_value']:.4f}) are both "
                    "non-stationary over the past 5 years — proceeding to Engle-Granger test."
                )
            else:
                for r in stationary:
                    st.warning(
                        f"⚠️ Alert: **{r['label']}** is stationary (ADF p={r['p_value']:.4f} < 0.05). "
                        "Cointegration requires non-stationary individual series — interpret results with caution."
                    )
                for r in non_stationary:
                    st.info(f"**{r['label']}** is non-stationary (ADF p={r['p_value']:.4f}).")
                st.info("Proceeding to Engle-Granger test.")

            st.markdown("---")

            # ── Section 2: Engle-Granger (both directions) ───────────────────
            st.subheader("Engle-Granger Test (both directions)")
            st.caption(
                "EG is not symmetric: regressing A on B vs B on A can produce different residuals "
                "and flip the verdict. The primary direction (lower p-value) drives the final verdict."
            )

            def _render_eg_pair(eg_res, eg_dir, is_primary, period_label, line_color):
                dep, indep = eg_dir.split("→")
                badge = "★ Primary direction" if is_primary else "Reverse direction"
                with st.container(border=True):
                    st.markdown(f"**{badge} — `{dep}` regressed on `{indep}`**")
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
                        title=f"Spread ({period_label}): {eg_dir}  [{spread.index[0].strftime('%b %Y')} → {spread.index[-1].strftime('%b %Y')}]",
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

            st.markdown("##### Past 5 Years")
            _render_eg_pair(cr["eg"],         cr["eg_direction"],         True,  "5yr", "#2196F3")
            _render_eg_pair(cr["eg_reverse"], cr["eg_reverse_direction"], False, "5yr", "#9C27B0")

            st.markdown("---")
            st.markdown("##### Past 2 Years")
            _render_eg_pair(cr["eg_2yr"],         cr["eg_direction_2yr"],         True,  "2yr", "#00897B")
            _render_eg_pair(cr["eg_reverse_2yr"], cr["eg_reverse_direction_2yr"], False, "2yr", "#F57C00")

            st.markdown("---")

            # ── Section 3: Quarterly P-Values ─────────────────────────────────
            st.subheader("Quarterly Cointegration P-Values — Past 1 Year")
            st.caption(
                "The past year is split into 4 equal quarters (Q1 = oldest, Q4 = most recent). "
                "Each quarter runs Engle-Granger in both directions. "
                "★ marks the primary direction (lower p-value), which determines pass/fail. "
                "Pass condition: p < 0.05."
            )

            q_cols = st.columns(4)
            for col, q in zip(q_cols, cr["quarters"]):
                with col:
                    with st.container(border=True):
                        start_str = q["start_date"].strftime("%b %d %Y")
                        end_str   = q["end_date"].strftime("%b %d %Y")
                        st.markdown(f"**{q['label']}**")
                        st.caption(f"{start_str} → {end_str}")

                        p_ab = q["eg_ab"]["p_value"]
                        p_ba = q["eg_ba"]["p_value"]
                        ab_is_primary = p_ab <= p_ba

                        # Direction A→B
                        ab_label = f"{'★ ' if ab_is_primary else ''}{coint_sym_a} regressed on {coint_sym_b}"
                        st.markdown(f"<small>{ab_label}</small>", unsafe_allow_html=True)
                        st.metric("p-value", f"{p_ab:.4f}", label_visibility="collapsed")

                        # Direction B→A
                        ba_label = f"{'★ ' if not ab_is_primary else ''}{coint_sym_b} regressed on {coint_sym_a}"
                        st.markdown(f"<small>{ba_label}</small>", unsafe_allow_html=True)
                        st.metric("p-value", f"{p_ba:.4f}", label_visibility="collapsed")

                        if q["passes"]:
                            st.success("Cointegrated ✓")
                        else:
                            st.error("Not cointegrated ✗")

            st.markdown("---")
            st.subheader("Final Verdict")
            st.caption(f"{cr['quarters_passing']}/4 quarters passed the cointegration test.")
            pair_conc = pair_conclusion(cr["pair_passes"])
            if cr["pair_passes"]:
                st.success(f"✓ {pair_conc}")
            else:
                st.error(f"✗ {pair_conc}")

# ─── Tab 4: Trading Signals ───────────────────────────────────────────────────
with tab_signals:
    st.header("Trading Signals — Quarterly β Pairs Strategy")
    st.caption(
        "Quarterly-fixed hedge ratio strategy: β is estimated from a trailing 1-year OLS "
        "and refreshed at each calendar-quarter boundary — fixed for the full quarter. "
        "Z-score uses a 60–120 day rolling mean/std. "
        "Best applied to pairs that pass the Cointegration Test. "
        "Signals: z < −2 → LONG spread, z > 2 → SHORT spread, |z| < 0.5 → EXIT."
    )

    ts_tickers = _tickers()
    ts_default_a = "ARM" if "ARM" in ts_tickers else ts_tickers[0]
    ts_others = [t for t in ts_tickers if t != ts_default_a]
    ts_default_b = "TSM" if "TSM" in ts_others else ts_others[0]

    tsc1, tsc2, tsc3 = st.columns([2, 2, 1])
    with tsc1:
        ts_sym_a = st.selectbox("Stock A", ts_tickers, index=ts_tickers.index(ts_default_a), key="ts_a")
    with tsc2:
        ts_b_opts = [t for t in ts_tickers if t != ts_sym_a]
        ts_sym_b = st.selectbox("Stock B", ts_b_opts,
                                index=ts_b_opts.index(ts_default_b) if ts_default_b in ts_b_opts else 0,
                                key="ts_b")
    with tsc3:
        ts_window = st.number_input("Z-score window (days)", min_value=60, max_value=120, value=90, step=10,
                                    help="Rolling window for z-score mean/std (60–120 days). β is always estimated from a trailing 1-year OLS, fixed per calendar quarter.",
                                    key="ts_win")

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
        cur_quarter = latest["quarter"] if "quarter" in latest.index else "—"
        st.caption(f"Quarter: {cur_quarter}  |  β is fixed for this quarter (estimated from trailing 1-year OLS).  "
                   f"Position B = {abs(latest['position_b']):.4f} units of {sym_b_lbl}  "
                   f"(position_B = β_q × |position_A|)")

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

        # ── Quarterly β chart ────────────────────────────────────────────────
        st.subheader("Quarterly Fixed Hedge Ratio β")
        st.caption("β is estimated once per calendar quarter from a trailing 1-year OLS — "
                   "the step-function shape shows each quarterly update.")
        fig_b = go.Figure()
        fig_b.add_trace(go.Scatter(
            x=valid.index, y=valid["beta"],
            mode="lines", name="β (quarterly fixed)",
            line=dict(color="#7B1FA2", width=2, shape="hv"),
        ))
        fig_b.add_hline(y=0, line=dict(color="#aaa", dash="dot", width=1))
        fig_b.update_layout(height=260, margin=dict(t=10), yaxis_title="β",
                            hovermode="x unified")
        st.plotly_chart(fig_b, use_container_width=True)

        # ── Recent signals table ────────────────────────────────────────────
        st.subheader("Recent Signal Log")
        recent = valid.tail(30).copy()
        recent["translation"] = recent.apply(lambda r: signal_translation(r, sym_a_lbl, sym_b_lbl), axis=1)
        display_cols = ["quarter", "z_score", "signal", "beta", "position_a", "position_b", "translation"]
        st.dataframe(
            recent[display_cols].rename(columns={
                "quarter": "Quarter", "z_score": "Z-Score", "signal": "Signal", "beta": "β (fixed)",
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
        f"**Train:** {train_start} → {train_end} (4 years, warms up quarterly β estimation)  |  "
        f"**Test:** {test_start} → {test_end} (most recent 1 year, evaluation only). "
        "β is estimated from a trailing 1-year OLS, fixed per calendar quarter. "
        "No DB writes. Results are fully in-memory."
    )

    bt_tickers = _tickers()
    bt_default_a = "ARM" if "ARM" in bt_tickers else bt_tickers[0]
    bt_others = [t for t in bt_tickers if t != bt_default_a]
    bt_default_b = "TSM" if "TSM" in bt_others else bt_others[0]

    btc1, btc2, btc3 = st.columns([2, 2, 1])
    with btc1:
        bt_sym_a = st.selectbox("Stock A", bt_tickers, index=bt_tickers.index(bt_default_a), key="bt_a")
    with btc2:
        bt_b_opts = [t for t in bt_tickers if t != bt_sym_a]
        bt_sym_b = st.selectbox("Stock B", bt_b_opts,
                                index=bt_b_opts.index(bt_default_b) if bt_default_b in bt_b_opts else 0,
                                key="bt_b")
    with btc3:
        bt_window = st.number_input("Z-score window (days)", min_value=60, max_value=120, value=90, step=10,
                                    help="Rolling window for z-score mean/std (60–120 days). β is always from trailing 1-year OLS, fixed per quarter.",
                                    key="bt_win")

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
            # Quarterly-fixed hedge ratio β
            beta_s = m["beta_series"]
            fig_beta = go.Figure()
            fig_beta.add_trace(go.Scatter(x=beta_s.index, y=beta_s.values, mode="lines",
                                          name="β (quarterly fixed)",
                                          line=dict(color="#F57C00", width=2, shape="hv")))
            fig_beta.add_hline(y=0, line=dict(color="#aaa", dash="dot", width=1))
            fig_beta.update_layout(title="Quarterly Fixed Hedge Ratio β (test period)",
                                   height=260, margin=dict(t=40), yaxis_title="β")
            st.plotly_chart(fig_beta, use_container_width=True)
            n_beta_changes = int((beta_s != beta_s.shift(1)).sum()) - 1
            st.caption(f"β updated {n_beta_changes} time(s) during test period  |  Std dev: {float(beta_s.std()):.4f}")

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

    # ── Section 1: Macro Regime Indicators ───────────────────────────────────
    st.subheader("Macro Regime Indicators")
    st.caption(
        "Live macro indicators — 10Y yield, real yields (TIPS), Nasdaq-100 breadth, VIX trend, "
        "and SMH/QQQ relative strength. Cached for 1 hour; click Refresh to force a reload."
    )

    ref_col, _ = st.columns([1, 5])
    with ref_col:
        if st.button("Refresh", key="refresh_regime_btn"):
            st.cache_data.clear()
            st.rerun()

    ind_df = None
    regime_alert_list = []
    with st.spinner("Fetching macro indicators — first load downloads ~100 tickers and may take ~30s…"):
        try:
            ind_df = _regime_indicators()
            regime_alert_list = detect_alerts(ind_df)
        except Exception as _exc:
            st.error(f"Failed to fetch macro indicators: {_exc}")

    if ind_df is not None and not ind_df.empty:
        latest = ind_df.dropna(how="all").iloc[-1]
        as_of  = ind_df.dropna(how="all").index[-1].date()

        # ── Snapshot row ──────────────────────────────────────────────────
        st.caption(f"As of {as_of}")
        sc1, sc2, sc3, sc4, sc5 = st.columns(5)

        ty_val   = latest.get("treasury_10y")
        tips_val = latest.get("tips_10y")
        br_val   = latest.get("nasdaq_breadth")
        vix_val  = latest.get("vix")
        ratio_val = latest.get("smh_qqq_ratio")

        sc1.metric("10Y Yield",    f"{ty_val:.3f}%"  if ty_val   is not None and not pd.isna(ty_val)   else "N/A")
        sc2.metric("TIPS Yield",   f"{tips_val:.2f}%" if tips_val is not None and not pd.isna(tips_val) else "N/A")
        sc3.metric("NDX Breadth",  f"{br_val:.1f}%"  if br_val   is not None and not pd.isna(br_val)   else "N/A")
        sc4.metric("VIX",          f"{vix_val:.1f}"  if vix_val  is not None and not pd.isna(vix_val)  else "N/A")
        sc5.metric("SMH/QQQ",      f"{ratio_val:.4f}" if ratio_val is not None and not pd.isna(ratio_val) else "N/A")

        st.markdown("---")

        # ── Alert rules grouped by indicator ─────────────────────────────
        _INDICATOR_ORDER = [
            "10Y Treasury Yield",
            "10Y TIPS Real Yield",
            "Nasdaq-100 Breadth",
            "VIX Trend",
            "SMH/QQQ Relative Strength",
        ]

        from collections import defaultdict
        by_indicator = defaultdict(list)
        for a in regime_alert_list:
            by_indicator[a["indicator"]].append(a)

        _SEV_ICON = {"critical": "🔴", "warning": "🟡", "info": "🟢"}

        for ind_name in _INDICATOR_ORDER:
            rules = by_indicator.get(ind_name, [])
            if not rules:
                continue

            # Header: red dot if any critical/warning triggered
            any_triggered = any(r["triggered"] for r in rules)
            any_critical  = any(r["triggered"] and r["severity"] == "critical" for r in rules)
            any_warning   = any(r["triggered"] and r["severity"] == "warning" for r in rules)
            header_icon = "🔴" if any_critical else ("🟡" if any_warning else "🟢")
            header_label = " — ⚡ ALERT" if any_triggered else " — OK"

            with st.expander(f"{header_icon} **{ind_name}**{header_label}", expanded=any_triggered):
                for rule in rules:
                    icon = _SEV_ICON.get(rule["severity"], "⚪")
                    cross_tag = " *(recently crossed)*" if rule["recently_crossed"] else ""

                    if rule["triggered"] and rule["severity"] == "critical":
                        st.error(f"{icon} **{rule['rule']}** — {rule['message']}{cross_tag}")
                    elif rule["triggered"] and rule["severity"] == "warning":
                        st.warning(f"{icon} **{rule['rule']}** — {rule['message']}{cross_tag}")
                    else:
                        st.success(f"{icon} **{rule['rule']}** — {rule['message']}")

        # ── Triggered-only summary table ──────────────────────────────────
        triggered_rules = [r for r in regime_alert_list if r["triggered"]]
        if triggered_rules:
            st.markdown("---")
            st.subheader("Active Alerts Summary")
            summary_rows = []
            for r in triggered_rules:
                summary_rows.append({
                    "Indicator": r["indicator"],
                    "Rule": r["rule"],
                    "Severity": r["severity"].upper(),
                    "New Cross": "Yes" if r["recently_crossed"] else "No",
                    "Details": r["message"],
                })
            st.dataframe(
                pd.DataFrame(summary_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No macro regime alerts currently triggered.")

    st.markdown("---")

    # ── Section 2: AI Regime Commentary ──────────────────────────────────────
    st.subheader("AI Regime Commentary")
    st.caption(
        "Claude writes a ~100-word briefing based on the current macro indicators and triggered alerts above. "
        "One commentary is stored per day — clicking Generate re-uses today's if it already exists."
    )

    alert = _alert_for_date(end_date)

    if alert and alert.get("commentary"):
        al1, al2 = st.columns([4, 1])
        with al1:
            st.markdown(alert["commentary"])
        with al2:
            st.metric("Generated for", str(alert["corr_date"]))
    else:
        st.info(
            f"No commentary stored for **{end_date}**. "
            "Click below — Claude will analyse the live macro indicators and write a briefing. "
            "First run fetches ~100 tickers and takes ~30–60 s."
        )
        if st.button("Generate Regime Commentary", type="primary", key="gen_alert_btn"):
            with st.spinner("Fetching indicators and generating commentary via Claude…"):
                try:
                    result = db.generate_alert_for_date(end_date)
                    if result:
                        _clear_and_rerun()
                    else:
                        st.error("ANTHROPIC_API_KEY is not set — cannot generate commentary.")
                except Exception as exc:
                    st.error(f"Generation failed: {exc}")

    alerts_df = _alerts(5)
    if not alerts_df.empty:
        st.markdown("---")
        st.subheader("Commentary History (last 5 days)")
        history = alerts_df[["generated_at", "corr_date", "commentary"]].copy()
        history.columns = ["Generated At", "Date", "Commentary"]
        st.dataframe(history, use_container_width=True, hide_index=True)

# ─── Tab 7: Fundamental Comparison ───────────────────────────────────────────
with tab_fund:
    st.header("Fundamental Comparison")
    st.caption(
        "Compare quarterly financials (income statement, balance sheet, cash flow) for any two tickers. "
        "Alerts fire on significant pair-level shifts, individual trend changes, and earnings quality red flags. "
        "Data from yfinance — cached 6 hours. Currency note: TSM reports in TWD."
    )

    # ── Stock pickers ────────────────────────────────────────────────────────
    fund_tickers = _tickers()
    if not fund_tickers:
        st.warning("No tickers in DB. Add tickers via Manage Tickers first.")
    else:
        _fd_default_a = "ARM" if "ARM" in fund_tickers else fund_tickers[0]
        _fd_others    = [t for t in fund_tickers if t != _fd_default_a]
        _fd_default_b = "TSM" if "TSM" in _fd_others else (_fd_others[0] if _fd_others else fund_tickers[0])

        fd_c1, fd_c2, fd_c3 = st.columns([2, 2, 1])
        with fd_c1:
            fd_sym_a = st.selectbox(
                "Stock A", fund_tickers,
                index=fund_tickers.index(_fd_default_a),
                key="fd_sym_a",
            )
        with fd_c2:
            fd_b_opts = [t for t in fund_tickers if t != fd_sym_a]
            fd_sym_b = st.selectbox(
                "Stock B", fd_b_opts,
                index=fd_b_opts.index(_fd_default_b) if _fd_default_b in fd_b_opts else 0,
                key="fd_sym_b",
            )
        with fd_c3:
            st.write("")
            fd_load = st.button("Load Fundamentals", type="primary", key="fd_load")

        if fd_load:
            with st.spinner(f"Fetching quarterly fundamentals for {fd_sym_a} and {fd_sym_b}…"):
                st.session_state["fd_data_a"]  = _fetch_fundamentals_cached(fd_sym_a)
                st.session_state["fd_data_b"]  = _fetch_fundamentals_cached(fd_sym_b)
                st.session_state["fd_loaded_a"] = fd_sym_a
                st.session_state["fd_loaded_b"] = fd_sym_b
                st.session_state.pop("fd_commentary", None)

        if "fd_data_a" not in st.session_state:
            st.info("Select two stocks above and click **Load Fundamentals** to begin.")
        else:
            fd_data_a   = st.session_state["fd_data_a"]
            fd_data_b   = st.session_state["fd_data_b"]
            fd_loaded_a = st.session_state.get("fd_loaded_a", fd_sym_a)
            fd_loaded_b = st.session_state.get("fd_loaded_b", fd_sym_b)

            if fd_loaded_a != fd_sym_a or fd_loaded_b != fd_sym_b:
                st.info(f"Showing data for **{fd_loaded_a}** / **{fd_loaded_b}**. Click Load to refresh for current selection.")

            if fd_data_a["n_quarters"] == 0 and fd_data_b["n_quarters"] == 0:
                st.error("Could not fetch fundamentals for either ticker. Check yfinance connectivity.")
            else:
                # Currency warnings
                for fd_d, fd_sym in [(fd_data_a, fd_loaded_a), (fd_data_b, fd_loaded_b)]:
                    if fd_d.get("currency", "USD") not in ("USD", ""):
                        st.warning(
                            f"⚠️  **{fd_sym}** reports in **{fd_d['currency']}** — all figures are "
                            f"in {fd_d['currency']} billions, not USD. Cross-stock comparisons are indicative only."
                        )

                # ── Helper: format a single metric value ─────────────────────
                _EPS_COLS     = {"EPS (Basic)", "EPS (Diluted)"}
                _PCT_COLS     = {"Gross Margin","Operating Margin","Net Margin","FCF Margin","R&D % Revenue"}
                _RATIO_COLS   = {"D/E Ratio","Current Ratio","OCF/NI"}
                _DAYS_COLS    = {"DSO","Inventory Days"}
                _PLAIN_COLS   = {"Accrual Ratio"}

                def _fmtv(val, col: str) -> str:
                    if val is None or (isinstance(val, float) and math.isnan(val)):
                        return "—"
                    if col in _EPS_COLS:
                        return f"${val:.2f}"
                    if col in _PCT_COLS:
                        return f"{val*100:.1f}%"
                    if col in _RATIO_COLS:
                        return f"{val:.2f}x"
                    if col in _DAYS_COLS:
                        return f"{val:.0f}d"
                    if col in _PLAIN_COLS:
                        return f"{val:.3f}"
                    return f"${val:.2f}B"

                def _delta_str(v0, v1, col: str) -> str:
                    if v0 is None or v1 is None or math.isnan(v0) or math.isnan(v1) or v1 == 0:
                        return ""
                    if col in _PCT_COLS:
                        chg = v0 - v1
                        arrow = "▲" if chg > 0 else "▼"
                        return f" {arrow}{abs(chg)*100:.1f}pp"
                    chg = (v0 - v1) / abs(v1)
                    arrow = "▲" if chg > 0 else "▼"
                    return f" {arrow}{abs(chg)*100:.0f}%"

                def _safe_v(df, col, idx=0):
                    if df.empty or col not in df.columns:
                        return float("nan")
                    try:
                        return float(df[col].iloc[idx])
                    except (IndexError, TypeError, ValueError):
                        return float("nan")

                # ── Helper: build a comparison DataFrame for a statement ──────
                def _build_cmp(stmt_key: str, metric_list: list) -> pd.DataFrame:
                    da = fd_data_a.get(stmt_key, pd.DataFrame())
                    db = fd_data_b.get(stmt_key, pd.DataFrame())
                    qa = fd_data_a.get("display_quarters", [])
                    qb = fd_data_b.get("display_quarters", [])
                    label_a = f"{fd_loaded_a} ({qa[0]})" if qa else fd_loaded_a
                    label_b = f"{fd_loaded_b} ({qb[0]})" if qb else fd_loaded_b

                    rows = []
                    for m in metric_list:
                        a_avail = (not da.empty) and (m in da.columns)
                        b_avail = (not db.empty) and (m in db.columns)
                        if not a_avail and not b_avail:
                            continue
                        v_a0 = _safe_v(da, m, 0); v_a1 = _safe_v(da, m, 1)
                        v_b0 = _safe_v(db, m, 0); v_b1 = _safe_v(db, m, 1)
                        rows.append({
                            "Metric":  m,
                            label_a:   (_fmtv(v_a0, m) + _delta_str(v_a0, v_a1, m)) if a_avail else "—",
                            label_b:   (_fmtv(v_b0, m) + _delta_str(v_b0, v_b1, m)) if b_avail else "—",
                        })
                    return pd.DataFrame(rows).set_index("Metric") if rows else pd.DataFrame()

                # ── Helper: bar chart for a metric over 4 quarters ───────────
                def _bar_chart(metric: str, stmt_key: str):
                    da = fd_data_a.get(stmt_key, pd.DataFrame())
                    db = fd_data_b.get(stmt_key, pd.DataFrame())
                    qa = fd_data_a.get("display_quarters", [])
                    qb = fd_data_b.get("display_quarters", [])

                    rows_a = [
                        {"Quarter": q, "Value": _safe_v(da, metric, i), "Stock": fd_loaded_a}
                        for i, q in enumerate(qa)
                        if not da.empty and metric in da.columns
                    ]
                    rows_b = [
                        {"Quarter": q, "Value": _safe_v(db, metric, i), "Stock": fd_loaded_b}
                        for i, q in enumerate(qb)
                        if not db.empty and metric in db.columns
                    ]
                    df_plot = pd.DataFrame(rows_a + rows_b).dropna(subset=["Value"])
                    if df_plot.empty:
                        return

                    is_pct = metric in _PCT_COLS
                    if is_pct:
                        df_plot["Value"] = df_plot["Value"] * 100

                    fig = px.bar(
                        df_plot, x="Quarter", y="Value", color="Stock",
                        barmode="group", title=metric,
                        labels={"Value": "%" if is_pct else "$B"},
                        color_discrete_sequence=["#636EFA", "#EF553B"],
                    )
                    fig.update_layout(height=280, margin=dict(t=35, b=0, l=0, r=0), legend_title_text="")
                    st.plotly_chart(fig, use_container_width=True)

                # ── Statement sub-tabs ───────────────────────────────────────
                fd_sub_inc, fd_sub_bal, fd_sub_cf, fd_sub_qual = st.tabs([
                    "Income Statement", "Balance Sheet", "Cash Flow", "Quality Ratios"
                ])

                with fd_sub_inc:
                    cmp = _build_cmp("income", INCOME_DISPLAY)
                    if not cmp.empty:
                        st.dataframe(cmp, use_container_width=True)
                    else:
                        st.info("No income statement data available.")
                    st.markdown("---")
                    ch1, ch2, ch3 = st.columns(3)
                    with ch1: _bar_chart("Revenue",       "income")
                    with ch2: _bar_chart("Net Income",    "income")
                    with ch3: _bar_chart("Gross Margin",  "derived")

                with fd_sub_bal:
                    cmp = _build_cmp("balance", BALANCE_DISPLAY)
                    if not cmp.empty:
                        st.dataframe(cmp, use_container_width=True)
                    else:
                        st.info("No balance sheet data available.")
                    st.markdown("---")
                    ch1, ch2, ch3 = st.columns(3)
                    with ch1: _bar_chart("Total Debt",         "balance")
                    with ch2: _bar_chart("Cash & Equivalents", "balance")
                    with ch3: _bar_chart("D/E Ratio",          "derived")

                with fd_sub_cf:
                    cmp = _build_cmp("cashflow", CASHFLOW_DISPLAY)
                    if not cmp.empty:
                        st.dataframe(cmp, use_container_width=True)
                    else:
                        st.info("No cash flow data available.")
                    st.markdown("---")
                    ch1, ch2, ch3 = st.columns(3)
                    with ch1: _bar_chart("Operating CF", "cashflow")
                    with ch2: _bar_chart("FCF",          "cashflow")
                    with ch3: _bar_chart("FCF Margin",   "derived")

                with fd_sub_qual:
                    st.caption(
                        "Derived quality ratios computed from the quarterly statements. "
                        "DSO/Inventory Days in days; margins in %; ratios are unitless."
                    )
                    cmp = _build_cmp("derived", DERIVED_DISPLAY)
                    if not cmp.empty:
                        st.dataframe(cmp, use_container_width=True)
                    else:
                        st.info("No derived ratio data available.")
                    st.markdown("---")
                    ch1, ch2, ch3 = st.columns(3)
                    with ch1: _bar_chart("OCF/NI",        "derived")
                    with ch2: _bar_chart("Accrual Ratio", "derived")
                    with ch3: _bar_chart("Current Ratio", "derived")

                # ── Alert panel ──────────────────────────────────────────────
                st.markdown("---")
                st.subheader("Alerts")

                fd_alerts = detect_fundamental_alerts(fd_data_a, fd_data_b)

                _SEV_ICON_FUND = {"Critical": "🔴", "Warning": "🟡", "Info": "🔵"}

                def _render_alert(a: dict):
                    icon = _SEV_ICON_FUND.get(a.get("severity", "Info"), "⚪")
                    msg  = a.get("message", "")
                    sev  = a.get("severity", "Info")
                    if sev == "Critical":
                        st.error(f"{icon} {msg}")
                    elif sev == "Warning":
                        st.warning(f"{icon} {msg}")
                    else:
                        st.info(f"{icon} {msg}")

                pair_alerts   = [a for a in fd_alerts if a.get("layer") == "pair"]
                indiv_alerts  = [a for a in fd_alerts if a.get("layer") in ("individual_trend", "quality")]

                al_col1, al_col2 = st.columns(2)

                with al_col1:
                    st.markdown(f"**Pair Shifts — {fd_loaded_a} vs {fd_loaded_b}**")
                    if pair_alerts:
                        for a in pair_alerts:
                            _render_alert(a)
                    else:
                        st.success("No significant pair shifts detected.")

                with al_col2:
                    st.markdown("**Individual Stock Alerts**")
                    if indiv_alerts:
                        for a in indiv_alerts:
                            _render_alert(a)
                    else:
                        st.success("No individual alerts triggered.")

                # ── Alert summary table ──────────────────────────────────────
                if fd_alerts:
                    with st.expander("Full alert table", expanded=False):
                        summary_rows = [{
                            "Layer":    a.get("layer", ""),
                            "Stock":    a.get("stock", a.get("metric", "")),
                            "Flag":     a.get("flag", a.get("metric", a.get("event_type", ""))),
                            "Severity": a.get("severity", ""),
                            "Message":  a.get("message", ""),
                        } for a in fd_alerts]
                        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

                # ── AI Commentary ─────────────────────────────────────────────
                st.markdown("---")
                st.subheader("AI Fundamental Briefing")
                st.caption(
                    "Claude analyses the metrics and triggered alerts above and writes a ~200-word "
                    "plain-English briefing covering relative strength, trends, and red flags."
                )

                if st.button("Generate AI Commentary", type="primary", key="fd_commentary_btn"):
                    with st.spinner("Asking Claude to analyse the fundamentals…"):
                        try:
                            text = generate_fundamental_commentary(fd_data_a, fd_data_b, fd_alerts)
                            st.session_state["fd_commentary"] = text
                        except Exception as exc:
                            st.error(f"Commentary generation failed: {exc}")

                if "fd_commentary" in st.session_state:
                    st.markdown(st.session_state["fd_commentary"])


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

