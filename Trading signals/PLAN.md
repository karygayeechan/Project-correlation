# Trading Signals — Implementation Plan

## Goal
Add two Streamlit tabs — **Trading Signals** and **Daily PnL** — that implement a
rolling pairs-trading strategy for any two tickers in the database, following the
8-step procedure in "Trading signals (rolling hedge ratio, large cap tech) instructions".

---

## Step 1 — Read the instruction file
Confirmed the 8-step procedure:
1. Z-score rolling window of 60–120 days (default 90) for mean/std computation
2. Quarterly-fixed hedge ratio β: at each calendar-quarter boundary, run OLS on trailing
   1-year (252-day) window — β is fixed for the entire quarter, not updated daily
3. Quarterly spread: spread_t = A_t − (α_q + β_q × B_t)  where α_q/β_q are the quarter's fixed values
4. Rolling z-score: z_t = (spread_t − μ_t) / σ_t  over the chosen 60–120 day window
5. Signals: z < −2 → LONG, z > 2 → SHORT, |z| < 0.5 → EXIT, else HOLD
6. Translate signals: LONG → buy 1 A / sell β_q B; SHORT → sell 1 A / buy β_q B
7. Position sizing uses the quarter's fixed β: position_B = −β_q × position_A
8. Daily PnL: PnL_t = pos_A_{t−1} × ΔA_t + pos_B_{t−1} × ΔB_t

---

## Step 2 — Create `Trading signals/trading_signals.py`
Pure computation module. No Streamlit imports.

### `fetch_prices(sym_a, sym_b) -> (pd.Series, pd.Series)`
- Queries `stock_prices` table via psycopg2 for the last 5 years
- Pivots to two aligned adj_close Series indexed by date
- Casts Decimal DB values to float

### `compute_rolling_signals(series_a, series_b, window=90) -> pd.DataFrame`
Core pipeline function. `window` controls z-score mean/std (60–120 days).

**Step 2 — Quarterly-fixed OLS β**
- Constants: `BETA_WINDOW = 252` (1-year trailing OLS), `WINDOW = 90` (z-score default)
- Identifies calendar-quarter boundaries in the date index (using `pd.PeriodIndex freq='Q'`)
- At each quarter boundary (and at the first eligible day after 252-day warmup):
  slices the past 252 rows and fits `OLS(A ~ const + B)` → new `cur_alpha`, `cur_beta`
- β is held constant until the next quarter boundary — no daily drift
- Early rows (< 252 days of history) remain NaN

**Step 3 — Quarterly spread**
- `spread_t = A_t − (α_q + β_q × B_t)` where α_q/β_q are the current quarter's fixed values
- Spread may show a small step at quarter boundaries when α/β update

**Step 4 — Rolling z-score**
- `roll_mean = spread.rolling(window).mean()`
- `roll_std  = spread.rolling(window).std()`
- `z_t = (spread_t − roll_mean_t) / roll_std_t`

**Step 5 — Raw signal**
- Vectorised np.where: z < −2 → LONG, z > 2 → SHORT, |z| < 0.5 → EXIT, else HOLD

**Steps 6 & 7 — Stateful positions**
- Scalar `cur_pos_a` carries state forward
- LONG → +1; SHORT → −1; EXIT → 0; HOLD → unchanged
- `position_b[i] = −β_q × cur_pos_a`  (β_q is the quarter's fixed value)

**Step 8 — Daily PnL**
- `delta_a = A.diff()`, `delta_b = B.diff()`
- `pnl_t = position_a.shift(1) × delta_a_t + position_b.shift(1) × delta_b_t`
- `cumulative_pnl = pnl.fillna(0).cumsum()`

### `signal_translation(row, sym_a, sym_b) -> str`
- Converts a signal row into a human-readable trade instruction (step 6 output)
- e.g. "BUY 1 NVDA  |  SELL 0.6926 TSM"

### Returns DataFrame with columns:
`price_a, price_b, quarter, alpha, beta, spread, rolling_mean, rolling_std,
z_score, signal, position_a, position_b, delta_a, delta_b, pnl, cumulative_pnl`
(`quarter` is the calendar quarter label, e.g. "2025Q3", showing which β is active)

---

## Step 3 — Update `app/streamlit_app.py`

### 3a — Imports
- `sys.path.insert` for `Trading signals/` directory
- `from trading_signals import fetch_prices as ts_fetch_prices, compute_rolling_signals, signal_translation`

### 3b — Tab list changes
- Removed: `"Price & Returns"` (tab_price) and its content block
- Added: `"Trading Signals"` (tab_signals) and `"Daily PnL"` (tab_pnl)
  inserted between `"Cointegration Test"` and `"Network Graph"`

### 3c — Trading Signals tab content
1. Pair selectors: Stock A (default NVDA), Stock B (default TSM), window input (default 90)
2. "Compute Signals" button triggers `fetch_prices` + `compute_rolling_signals`
3. Results stored in `st.session_state["ts_df"]` so the Daily PnL tab can reuse them
4. **Current signal panel**: Signal label, Z-Score, β, position_A size, trade instruction string
5. **Z-score chart**: line series with ±2 (dash) and ±0.5 (dot) threshold lines;
   signal-coloured scatter overlay (LONG=blue, SHORT=red, EXIT=orange, HOLD=grey)
6. **Rolling β chart**: line chart of β_t over time
7. **Recent signal log**: last 30 rows reversed, showing z-score, signal, β,
   position sizes, and trade instruction per day

### 3d — Daily PnL tab content
- Reads `st.session_state["ts_df"]`; prompts user to compute signals first if empty
1. **Summary metrics row**: Total PnL, Sharpe Ratio, Max Drawdown, Win Rate, Active Days
   - Sharpe = mean(pnl) / std(pnl) × √252
   - Max Drawdown = min(cumulative_pnl − running_max)
   - Win Rate = fraction of active-position days with pnl > 0
2. **Cumulative PnL chart**: filled area line, zero baseline
3. **Daily PnL bar chart**: green bars for gains, red bars for losses
4. **Monthly PnL breakdown**: bar chart resampled to month-end

---

## Step 4 — Restart Streamlit
- Killed existing process with `pkill -f "streamlit run"`
- Relaunched so the new `Trading signals` module import resolves cleanly
- Verified no errors in `/tmp/streamlit.log`

---

## File structure after implementation
```
Trading signals/
  Trading signals (rolling hedge ratio, large cap tech) instructions  # original spec
  PLAN.md                          # this file
  trading_signals.py               # computation logic (steps 1–8)
```
`app/streamlit_app.py`             # modified: removed Price & Returns, added Trading Signals + Daily PnL tabs

---

## Key design decisions

| Decision | Reason |
|----------|--------|
| Quarterly β, not daily rolling | Eliminates daily β noise and negative-beta windows; matches how a real desk re-estimates hedge ratios |
| Trailing 1-year OLS for β | More data → stabler estimate; 252 days balances recency with variance |
| β fixed per calendar quarter | Clean boundaries (Q1/Q2/Q3/Q4); easy to audit which β is live |
| Z-score window 60–120 days | Separate from β estimation; user-configurable in the UI |
| `position_b = −β_q × position_a` (negative sign) | LONG spread = buy A / sell B; SHORT = sell A / buy B — direction encoded in sign of position_a |
| `pnl uses .shift(1)` | Position entered at close of t−1 earns the price move from t−1 to t |
| Results in `session_state` | Avoids recomputing when user switches to Daily PnL tab |
| No cointegration gate | User selects any pair; caption notes pairs should ideally pass the Cointegration Test first |
