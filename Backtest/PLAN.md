# Backtest Tab — Implementation Plan

## What was built

### New file: `Backtest/backtest.py`
All computation logic lives here. The Streamlit tab calls these functions; nothing touches the DB.

**`get_split_dates()`**
Returns `(train_start, train_end, test_start, test_end)` based on today's date.
- Test period = most recent 1 calendar year
- Train period = 4 years before that

**`run_backtest(sym_a, sym_b, window=90)`**
1. Calls `fetch_prices` from `Trading signals/trading_signals.py` — same 5-year DB fetch used by the Trading Signals tab.
2. Calls `compute_rolling_signals` on the **full** 5-year history — the 4-year training window warms up the rolling hedge ratio so the model is calibrated by the time the test period begins.
3. Slices the resulting DataFrame to `[test_start : test_end]` and returns both `(full_df, test_df)`.
4. No DB writes. No changes to other tabs.

**`identify_trades(df)`**
Groups consecutive non-zero `position_a` blocks into discrete trade records.
Handles mid-stream position flips (LONG→SHORT without an explicit EXIT signal).
Returns a DataFrame with `entry_date`, `exit_date`, `direction`, `holding_days`, `pnl`.

**`compute_halflife(spread)`**
Fits an AR(1) OLS on spread differences: `Δspread = γ · spread_{t-1} + ε`.
Half-life = `−ln(2) / γ`. Returns `nan` if the series is not mean-reverting (γ ≥ 0).

**`compute_all_metrics(test_df)`**
Master function that computes every metric the tab displays. Returns a single flat dict of scalars, Series, and DataFrames. Key sections:

| Section | Key metrics |
|---|---|
| Performance | Total PnL, annualized return %, Sharpe + label, quarterly Sharpe, rolling 30d/60d Sharpe, cumulative PnL, drawdown, max drawdown, Calmar + label, win rate, avg profit/trade, 5th/95th pct trade PnL |
| Trading activity | # trades, avg holding period, half-life + label, total turnover, Sharpe at 0/1/5/10 bps cost + strategy verdict |
| Risk | Ann. volatility, skewness, excess kurtosis, VaR 95%, CVaR 95%, max losing streak (days + value) |
| Stability | Rolling 60-day ADF p-value on spread, z-score histogram, hedge ratio β series, rolling 60-day half-life series, std dev of trade PnL |
| Scalability | Metrics recomputed at 1×/2×/5× position size |

**Annualized return convention:** `mean(daily_pnl) × 252 / mean(price_a)` expressed as %. The mean price of stock A is used as the capital proxy (1 unit position size assumed throughout).

**Calmar ratio:** `ann_pnl_$ / |max_drawdown_$|` — both in dollar terms to keep the ratio consistent with the $ PnL framing of the rest of the strategy.

**Transaction cost sensitivity:** For each bps scenario, daily cost = `|Δposition_a| × price_a × bps/10000 + |Δposition_b| × price_b × bps/10000`. Sharpe is recomputed on cost-adjusted PnL.

---

## Changes to `app/streamlit_app.py`

1. **Added import** of `run_backtest`, `compute_all_metrics`, `get_split_dates` from `Backtest/backtest.py` via `sys.path.insert`.

2. **Removed Volatility tab** (`tab_vol`) from the `st.tabs()` call and deleted its `with tab_vol:` block entirely.

3. **Inserted Backtest tab** (`tab_test`) between Trading Signals and Daily PnL in the tab list.

4. **Backtest tab layout** (5 sections, shown after "Run Backtest" is clicked):
   - Section 1 — Performance: 6-metric top row, 5th/95th pct trade row, quarterly Sharpe table, rolling Sharpe chart, cumulative PnL + drawdown overlay chart.
   - Section 2 — Trading Activity: 4-metric row, cost-sensitivity table + verdict, trade log table.
   - Section 3 — Risk Metrics: 5-metric row (vol, skew, kurt, VaR, CVaR), max losing streak row.
   - Section 4 — Stability: 2×2 chart grid (rolling ADF p-value, z-score histogram, β series, rolling half-life) with std dev captions.
   - Section 5 — Scalability: text-only comparison of 2× and 5× vs baseline (Sharpe is scale-invariant so differences are only in $ metrics).

5. Results stored in `st.session_state["bt_result"]` — independent of the Trading Signals tab's `ts_df` state.

---

## Design decisions

- **Full 5-year warm-up, 1-year test slice:** Running signals on the full history before slicing avoids any "cold start" artifact in the rolling OLS during the test period. This correctly simulates live deployment where the model has been running for 4 years before the test window starts.
- **No DB modifications:** The backtest is entirely in-memory. `fetch_prices` reads from the DB (read-only) and `compute_rolling_signals` is a pure function.
- **Capital proxy = mean(price_a):** Since position sizing is always ±1 unit of stock A, using the mean price of A over the test period as the capital denominator is a reasonable approximation for % return calculation.
- **Scalability section is text-only (per instruction):** Sharpe is mathematically scale-invariant (multiplying PnL by a constant leaves mean/std unchanged), so only absolute metrics (total PnL, max DD) change. This is surfaced as text rather than charts to avoid visual clutter.
