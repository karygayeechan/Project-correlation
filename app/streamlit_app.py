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
    date_range = st.date_input(
        "Date Range",
        value=(one_year_ago, today),
        max_value=today,
        help="Global date window used by all charts.",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = one_year_ago, today

    st.markdown("---")
    st.caption(f"{len(db_tickers)} ticker(s) in DB")

# ─── Tabs ─────────────────────────────────────────────────────────────────────
(
    tab_heat,
    tab_roll,
    tab_price,
    tab_scatter,
    tab_network,
    tab_vol,
    tab_manage,
    tab_log,
) = st.tabs([
    "Correlation Heatmap",
    "Rolling Correlation",
    "Price & Returns",
    "Pair Scatter",
    "Network Graph",
    "Volatility",
    "Manage Tickers",
    "ETL Log",
])

# ─── Tab 1: Correlation Heatmap ───────────────────────────────────────────────
with tab_heat:
    st.header("Correlation Heatmap")
    st.caption(
        "Pairwise Pearson correlation of daily returns, computed from DB prices. "
        "Shift the end date to explore historical regimes."
    )

    if len(selected) < 2:
        st.info("Select at least 2 tickers in the sidebar.")
    else:
        ctrl1, ctrl2 = st.columns([1, 2])
        with ctrl1:
            period = st.radio("Period", ["1m", "6m"], horizontal=True, key="heat_period",
                              help="1m = last 21 trading days, 6m = last 126")
        with ctrl2:
            heat_end = st.date_input(
                "Analysis end date",
                value=end_date,
                max_value=today,
                key="heat_end",
                help="Slide back to compare correlation snapshots across time.",
            )

        with st.spinner("Computing correlations..."):
            corr_mat = _corr_heatmap(tuple(sorted(selected)), period, heat_end)

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
                title=f"{period} Correlation — ending {heat_end}",
                height=480,
                xaxis=dict(side="bottom"),
                margin=dict(l=10, r=10, t=50, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

            # Ranked pairs table
            pairs = []
            for i in range(len(labels)):
                for j in range(i + 1, len(labels)):
                    pairs.append({"Pair": f"{labels[i]} / {labels[j]}", "r": round(z[i][j], 4)})
            if pairs:
                pairs_df = pd.DataFrame(pairs).sort_values("r", ascending=False, key=abs)
                st.subheader("Ranked Pairs")
                st.dataframe(pairs_df, use_container_width=True, hide_index=True)

# ─── Tab 2: Rolling Correlation ───────────────────────────────────────────────
with tab_roll:
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
            rc_sym1 = st.selectbox("Ticker 1", selected, key="rc_sym1")
        with c2:
            other = [t for t in selected if t != rc_sym1]
            rc_sym2 = st.selectbox("Ticker 2", other or selected, key="rc_sym2")
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
                title=f"Rolling {window_label} Correlation: {rc_sym1} vs {rc_sym2}",
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

# ─── Tab 3: Price & Returns ───────────────────────────────────────────────────
with tab_price:
    st.header("Normalized Price & Daily Returns")
    st.caption(
        "Price is indexed to 100 at the start of the window so tickers with different "
        "price levels are directly comparable. Returns are daily % change of adjusted close."
    )

    if not selected:
        st.info("Select tickers in the sidebar.")
    else:
        with st.spinner("Loading price data..."):
            prices = _stock_prices(tuple(sorted(selected)), start_date, end_date)

        if prices.empty:
            st.info("No price data for the selected range.")
        else:
            pivot = prices.pivot(index="date", columns="symbol", values="adj_close").dropna(how="all")
            view = st.radio("Show", ["Normalized Price", "Daily Returns", "Both"], horizontal=True, key="price_view")

            if view in ("Normalized Price", "Both"):
                norm = pivot.div(pivot.bfill().iloc[0]) * 100
                fig_n = px.line(
                    norm.reset_index().melt(id_vars="date", var_name="Symbol", value_name="Index"),
                    x="date", y="Index", color="Symbol",
                    title="Normalized Price (base = 100 at window start)",
                )
                fig_n.add_hline(y=100, line_dash="dot", line_color="#aaa", line_width=1)
                fig_n.update_layout(height=400, hovermode="x unified", margin=dict(t=50))
                st.plotly_chart(fig_n, use_container_width=True)

            if view in ("Daily Returns", "Both"):
                ret = (pivot.pct_change() * 100).reset_index().melt(
                    id_vars="date", var_name="Symbol", value_name="Return (%)"
                )
                fig_r = px.bar(
                    ret.dropna(),
                    x="date", y="Return (%)", color="Symbol",
                    barmode="group",
                    title="Daily Returns (%)",
                )
                fig_r.update_layout(height=400, hovermode="x unified", margin=dict(t=50))
                st.plotly_chart(fig_r, use_container_width=True)

# ─── Tab 4: Pair Scatter ─────────────────────────────────────────────────────
with tab_scatter:
    st.header("Pair Return Scatter")
    st.caption(
        "Each point is one trading day. The slope (beta) shows how much one ticker moves "
        "per unit move of the other. Pearson r measures linear co-movement."
    )

    if len(selected) < 2:
        st.info("Select at least 2 tickers in the sidebar.")
    else:
        sc1, sc2 = st.columns(2)
        with sc1:
            sc_sym1 = st.selectbox("Ticker 1", selected, key="sc_sym1")
        with sc2:
            sc_other = [t for t in selected if t != sc_sym1]
            sc_sym2 = st.selectbox("Ticker 2", sc_other or selected, key="sc_sym2")

        with st.spinner("Loading pair data..."):
            sc_prices = _stock_prices((sc_sym1, sc_sym2), start_date, end_date)

        if sc_prices.empty:
            st.info("No data available.")
        else:
            sc_pivot = sc_prices.pivot(index="date", columns="symbol", values="adj_close")
            sc_ret = (sc_pivot.pct_change() * 100).dropna()

            if sc_sym1 not in sc_ret.columns or sc_sym2 not in sc_ret.columns:
                st.info("Insufficient overlapping data.")
            else:
                x_vals = sc_ret[sc_sym1].values
                y_vals = sc_ret[sc_sym2].values
                pearson_r = float(np.corrcoef(x_vals, y_vals)[0, 1])
                beta = float(np.polyfit(x_vals, y_vals, 1)[0])

                x_line = np.linspace(x_vals.min(), x_vals.max(), 100)
                y_line = np.polyval([beta, np.polyfit(x_vals, y_vals, 1)[1]], x_line)

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=x_vals, y=y_vals,
                    mode="markers",
                    name="Daily returns",
                    marker=dict(size=5, opacity=0.55, color="#2196F3"),
                ))
                fig.add_trace(go.Scatter(
                    x=x_line, y=y_line,
                    mode="lines",
                    name=f"OLS  r={pearson_r:.3f}",
                    line=dict(color="#e53935", dash="dash", width=2),
                ))
                fig.update_layout(
                    title=f"{sc_sym1} vs {sc_sym2}  |  Pearson r = {pearson_r:.3f}",
                    xaxis_title=f"{sc_sym1} Daily Return (%)",
                    yaxis_title=f"{sc_sym2} Daily Return (%)",
                    height=500,
                    margin=dict(t=50),
                )
                st.plotly_chart(fig, use_container_width=True)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Pearson r", f"{pearson_r:.4f}")
                m2.metric("R²", f"{pearson_r**2:.4f}")
                m3.metric(f"Beta ({sc_sym2}/{sc_sym1})", f"{beta:.4f}")
                m4.metric("Observations", str(len(x_vals)))

# ─── Tab 5: Correlation Network ───────────────────────────────────────────────
with tab_network:
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

            # Edges
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

            # Nodes
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

# ─── Tab 6: Volatility Tracker ────────────────────────────────────────────────
with tab_vol:
    st.header("Volatility Tracker")
    st.caption(
        "Rolling annualized realized volatility = rolling std of daily returns × √252. "
        "Higher vol often coincides with correlation spikes — both are risk signals."
    )

    if not selected:
        st.info("Select tickers in the sidebar.")
    else:
        vol_window_label = st.select_slider(
            "Rolling window", options=["10d", "21d", "42d", "63d"], value="21d", key="vol_win"
        )
        vol_win = int(vol_window_label.replace("d", ""))

        with st.spinner("Loading price data..."):
            vol_prices = _stock_prices(tuple(sorted(selected)), start_date, end_date)

        if vol_prices.empty:
            st.info("No price data.")
        else:
            vp = vol_prices.pivot(index="date", columns="symbol", values="adj_close")
            vol = vp.pct_change().rolling(vol_win).std() * (252 ** 0.5) * 100

            fig_vol = px.line(
                vol.reset_index().melt(id_vars="date", var_name="Symbol", value_name="Ann. Vol (%)"),
                x="date", y="Ann. Vol (%)", color="Symbol",
                title=f"Rolling {vol_window_label} Annualized Volatility",
            )
            fig_vol.update_layout(height=420, hovermode="x unified", margin=dict(t=50))
            st.plotly_chart(fig_vol, use_container_width=True)

            latest_vol = vol.iloc[-1].dropna().sort_values(ascending=False)
            if not latest_vol.empty:
                st.subheader("Latest Volatility Snapshot")
                vdf = latest_vol.reset_index()
                vdf.columns = ["Ticker", "Ann. Vol (%)"]
                vdf["Ann. Vol (%)"] = vdf["Ann. Vol (%)"].round(2)
                st.dataframe(vdf, use_container_width=True, hide_index=True)

# ─── Tab 7: Manage Tickers ────────────────────────────────────────────────────
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
        "Fetches 1y of price data for the new ticker from yfinance, inserts it, "
        "then recomputes correlations for all active tickers."
    )
    add_col1, add_col2 = st.columns([2, 1])
    with add_col1:
        new_ticker = st.text_input("Symbol (e.g. MSFT)", key="add_input").strip().upper()
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
    st.caption("Deletes the ticker's prices and correlations from DB. Other pairs are unaffected.")
    if db_tickers:
        rm_col1, rm_col2 = st.columns([2, 1])
        with rm_col1:
            to_remove = st.selectbox("Ticker to remove", db_tickers, key="rm_select")
        with rm_col2:
            st.write("")
            st.write("")
            rm_btn = st.button("Remove", type="secondary", key="rm_btn")

        if rm_btn:
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
        "Re-fetches the latest 1y of prices for all DB tickers, updates stock_prices, "
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

# ─── Tab 8: ETL Log ───────────────────────────────────────────────────────────
with tab_log:
    st.header("ETL Log")
    st.caption(
        "Every pipeline run appends a row here — whether triggered from 'Manage Tickers' "
        "or the CLI. Status `error` rows include the exception message."
    )

    log_df = _etl_log(50)

    if log_df.empty:
        st.info("No ETL runs recorded yet. Run the ETL from 'Manage Tickers' or via `python3 etl/load.py`.")
    else:
        # Reorder columns for readability
        cols = ["run_at", "status", "tickers", "rows_inserted", "rows_skipped", "duration_sec", "error_msg"]
        cols = [c for c in cols if c in log_df.columns]
        st.dataframe(
            log_df[cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "run_at": st.column_config.DatetimeColumn("Run At", format="YYYY-MM-DD HH:mm:ss"),
                "status": st.column_config.TextColumn("Status"),
                "tickers": st.column_config.TextColumn("Tickers"),
                "rows_inserted": st.column_config.NumberColumn("Inserted", format="%d"),
                "rows_skipped": st.column_config.NumberColumn("Skipped", format="%d"),
                "duration_sec": st.column_config.NumberColumn("Duration (s)", format="%.2f"),
                "error_msg": st.column_config.TextColumn("Error"),
            },
        )

        # Summary metrics
        total = len(log_df)
        successes = (log_df["status"] == "success").sum()
        last_run = log_df["run_at"].iloc[0] if not log_df.empty else None
        s1, s2, s3 = st.columns(3)
        s1.metric("Total runs", total)
        s2.metric("Successful", int(successes))
        s3.metric("Last run", str(last_run)[:19] if last_run is not None else "—")
