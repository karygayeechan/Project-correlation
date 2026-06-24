# Trading Signals — Implementation Plan

## Goal
Generate pairs-trading signals for any two tickers that have passed the cointegration test
(Path 1 or Path 2 — see `Cointegration test/PLAN.md`). Parameters (α, β) are sourced from the
relevant cointegration window and re-estimated monthly. A half-life gate and a four-trigger
kill switch govern trade entry and disabling.

---

## Prerequisite — Cointegration pass

Only pairs that pass the cointegration test under Path 1 or Path 2 are eligible for trading.
The pass scenario determines which window is used for parameter estimation.

| Pass path | Parameter window |
|-----------|-----------------|
| Path 1 (5yr + 2yr same direction) | 2-year window — use α and β from the 2yr OLS |
| Path 2 (post-break + >2yr since break) | Post-break window — use α and β from the OLS over [ZA break date → today] |

---

## Parameter estimation and monthly re-estimation

### Initial parameters
- **Scenario 1 (Path 1):** Take α and β from the most recent 2-year OLS (the same window used
  in the cointegration test).
- **Scenario 2 (Path 2):** Take α and β from the post-break OLS (ZA break date → today).

### Monthly re-estimation
At each monthly boundary, extend the estimation window by 1 month and re-run the cointegration
test on the updated window:

- **Scenario 1:** New window = original 2yr + accumulated months of new data
  (e.g. after 1 month: 1yr 11m + 1m new data = 2yr OLS; after 2 months: 1yr 10m + 2m, etc.)
- **Scenario 2:** New window = [ZA break date → today] extended by the accumulated new months.

If the cointegration test still passes on the updated window → use the updated α and β for the
next trading month. If cointegration no longer passes → stop trading / no new transactions.

These α and β are held fixed for the entire next trading month (not updated day-to-day).

---

## Step-by-step signal generation

### Step 1 — Z-score rolling window
Choose a z-score rolling window of 60–120 days (default 90).

### Step 2 — Spread and half-life
Using the fixed α and β for the current period:

```
spread_t = A_t − (α + β × B_t)
```

Compute half-life from the spread via OLS:
```
ΔSpread_t = c + θ · Spread_{t−1} + ε_t
half_life = −ln(2) / θ
```

Freeze the half-life estimate for the next trading period (do not update daily).

**Entry gate:** Only enter a trade if `3 < half_life < 40`.
If half-life is outside this range, no new positions are opened.

### Step 3 — Rolling z-score
```
z_t = (spread_t − μ_t) / σ_t
```
where μ_t and σ_t are the rolling mean and std of the spread over the chosen window.

### Step 4 — Trading signals
```
z < −2      → LONG spread
z >  2      → SHORT spread
|z| < 0.5   → EXIT the trade
otherwise   → HOLD
```

### Step 5 — Signal translation
```
LONG spread  (z < −2): BUY  A  |  SELL β × B
SHORT spread (z >  2): SELL A  |  BUY  β × B
```

### Step 6 — Position sizing
```
position_B = −β × position_A
```
β does not change day-to-day within the monthly period.

### Step 7 — Daily PnL
```
PnL_t = position_A_{t−1} × ΔA_t + position_B_{t−1} × ΔB_t
```

---

## Kill switch (hard stop)

Disable the pair (no new transactions) if ANY of the following triggers fire:

| Trigger | Condition |
|---------|-----------|
| Cointegration failure | p-value > 0.05 in the trading direction (Y = A, X = B) |
| Half-life breach | Half-life doubles from its frozen estimate, OR half-life < 3, OR half-life > 40 |
| β drift | Monthly re-estimated β deviates > 20% from **beta_init** (the β at the time of the cointegration pass) |
| Volatility regime | R_t = σ_20 / σ_100 > 1.8 (see volatility filter below) |

### Volatility regime filter
1. Compute 20-day EWMA volatility (σ_20) and 100-day rolling volatility (σ_100) on the spread.
2. Compute ratio `R_t = σ_20 / σ_100`.

| R_t range | Action |
|-----------|--------|
| R < 1.3 | Normal — full position |
| 1.3 ≤ R < 1.8 | Elevated — halve position size |
| R ≥ 1.8 | Kill switch — stop trading |

Display the current volatility regime (R_t value + status label) on the Streamlit Trading
Signals tab.

---

## Module: `trading_signals.py`

### `fetch_prices(sym_a, sym_b) -> (pd.Series, pd.Series)`
- Queries `stock_prices` for the last 5 years via psycopg2.
- Pivots to two aligned adj_close Series indexed by date. Casts Decimal → float.

### `compute_half_life(spread: pd.Series) -> float`
- Regresses ΔSpread on lag(Spread) + const via OLS.
- Returns `−ln(2) / θ` where θ is the lag coefficient.
- Returns NaN if θ ≥ 0 (non-mean-reverting).

### `compute_volatility_regime(spread: pd.Series) -> pd.DataFrame`
- 20-day EWMA std and 100-day rolling std of the spread.
- `R_t = ewma_20 / rolling_100`.
- Returns DataFrame with columns: `ewma_20`, `rolling_100`, `ratio`, `regime`
  (regime: "Normal" / "Elevated" / "Kill Switch").

### `compute_rolling_signals(series_a, series_b, alpha, beta, window=90) -> pd.DataFrame`
Core pipeline. α and β are passed in (sourced from the relevant cointegration window).

1. Spread: `spread_t = A_t − (α + β × B_t)`
2. Half-life: computed from spread; frozen for the period.
3. Rolling z-score: `(spread − roll_mean) / roll_std` over `window` days.
4. Signals: vectorised np.where → LONG / SHORT / EXIT / HOLD.
5. Stateful positions: `cur_pos_a` carries state forward; `position_b = −β × position_a`.
   Position size is halved when volatility regime is "Elevated".
   Position is forced to 0 when kill switch fires.
6. Daily PnL: `pnl_t = pos_a_{t−1} × ΔA_t + pos_b_{t−1} × ΔB_t`.
7. `cumulative_pnl = pnl.fillna(0).cumsum()`.

Returns DataFrame with columns:
`price_a, price_b, alpha, beta, spread, rolling_mean, rolling_std,
z_score, signal, position_a, position_b, delta_a, delta_b, pnl, cumulative_pnl,
half_life, vol_ratio, vol_regime, kill_switch`

### `signal_translation(row, sym_a, sym_b) -> str`
Converts a signal row into a human-readable trade instruction.
e.g. `"BUY 1 JPM  |  SELL 0.8321 BAC"`

---

## Streamlit tab: Trading Signals

1. Pair selectors (default JPM / BAC), z-score window input (default 90).
2. Cointegration pass path indicator (Path 1 / Path 2 / Not passed).
3. "Compute Signals" button → runs `fetch_prices` + `compute_rolling_signals`.
   Results stored in `st.session_state["ts_df"]`.
4. **Volatility regime panel**: current R_t value, regime label (Normal / Elevated / Kill Switch).
5. **Current signal panel**: signal label, z-score, β, half-life, position_A size, kill switch
   status, trade instruction string.
6. **Z-score chart**: line with ±2 (dash) and ±0.5 (dot) threshold lines;
   signal-coloured scatter overlay (LONG=blue, SHORT=red, EXIT=orange, HOLD=grey).
7. **Rolling β chart**: β over time with 20% deviation bands around trading β.
8. **Volatility regime chart**: R_t line with 1.3 and 1.8 threshold lines.
9. **Recent signal log**: last 30 rows showing z-score, signal, β, half-life, vol_regime,
   position sizes, and trade instruction.

---

## Streamlit tab: Daily PnL

Reads `st.session_state["ts_df"]`; prompts user to compute signals first if empty.

1. **Summary metrics**: Total PnL, Sharpe (mean/std × √252), Max Drawdown,
   Win Rate, Active Days.
2. **Cumulative PnL chart**: filled area line, zero baseline.
3. **Daily PnL bar chart**: green/red bars.
4. **Monthly PnL breakdown**: bar chart resampled to month-end.

---

## Key design decisions

| Decision | Reason |
|----------|--------|
| Scenario-based parameter window | α and β sourced from the window that produced the cointegration pass (2yr for Path 1, post-break for Path 2) — ensures parameters reflect the actual relationship being traded |
| Monthly re-estimation with cointegration re-check | Adapts to drift while preventing trading when the relationship has broken; rolling forward the full prior window avoids overfitting to a single month |
| Parameters fixed within the monthly period | Prevents daily α/β noise from generating spurious signals; mirrors how a real desk re-estimates hedge ratios |
| Half-life gate (3–40 days) | Ensures the spread is mean-reverting fast enough to trade profitably but not so fast that it is pure noise; estimated from spread, not individual series |
| Half-life frozen per period | Consistent with the fixed-parameter philosophy; prevents the gate from flickering daily |
| Four-trigger kill switch | Each trigger addresses a distinct failure mode: model breakdown (cointegration), mean-reversion breakdown (half-life), hedge mismatch (β drift), market stress (volatility) |
| β-drift compares against beta_init, not current monthly β | The kill switch measures cumulative drift of the relationship since the cointegration pass was granted. Comparing the monthly re-estimated β against a β computed over just one month's window would be noisy and conceptually wrong — we care whether the long-run relationship has drifted from its validated state, not whether this month's snapshot differs from last month's. |
| Volatility regime: halve then stop | Gradual response to rising volatility rather than a binary on/off; 1.3/1.8 thresholds calibrated to common risk management practice |
| `position_b = −β × position_a` (negative sign) | LONG spread = buy A / sell B; SHORT = sell A / buy B — direction encoded in sign of position_a |
| PnL uses `.shift(1)` | Position entered at close of t−1 earns the price move from t−1 to t |
| Results in `session_state` | Avoids recomputing when user switches to Daily PnL tab |
