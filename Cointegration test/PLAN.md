# Cointegration Test — Implementation Plan

## Goal
Cointegration test tab in Streamlit. Three data windows, each with its own independent OLS.

| Window | Data period | OLS | Used for |
|---|---|---|---|
| **5-year** | 365 × 5 cal days | Fresh on 5yr data | I(1) check; EG test; rolling β reference; verdict |
| **2-year** | 365 × 2 cal days | Fresh on 2yr data | EG confirmation; β comparison vs 5yr; verdict |
| **1-year** | 365 cal days | Fresh per ~63-day quarter | Quarterly display only (not verdict) |

All data from DB (`stock_prices`), `end = date.today()`. All computations use **log(price)**.

---

## Module: `Cointegration test/cointegration.py`

### `fetch_prices(sym_a, sym_b, days) -> (Series, Series)`
Fetches adj_close from DB; returns aligned raw price Series. Log transform applied downstream.

### `run_adf(series, label) -> dict`
Tests log(price) for I(1). `autolag='AIC'` used for all adfuller calls.
- **Level**: `adfuller(log_price, autolag='AIC')`
- **Diff**: `adfuller(log_price.diff().dropna(), autolag='AIC')`
- `is_i1 = (level p > 0.05) AND (diff p < 0.05)`
- Returns: `label, stat, p_value, critical_values, is_stationary, verdict, diff_p_value, is_diff_stationary, is_i1`

### `run_engle_granger(series_a, series_b) -> dict`
OLS on log prices + ADF on residuals. Called with whatever window's data is passed in —
the α and β it returns are specific to that window.
- `log(A) = α + β · log(B) + ε` — OLS with intercept
- `adfuller(residuals, autolag='AIC')`
- Returns: `alpha, beta, residuals, stat, p_value, critical_values, is_cointegrated, verdict`

### `compute_rolling_beta(series_y, series_x, window=252) -> pd.Series`
Rolling OLS β for `log(Y) = α + β·log(X)`. Vectorized closed-form formula.
Computed separately from EG tests — for stability diagnostics only.

### `run_all(sym_a, sym_b) -> dict`

**5yr phase:**
1. `run_adf` on each log-price series
2. `run_engle_granger` in both directions on 5yr data → `eg_ab_5yr`, `eg_ba_5yr`
3. Primary = lower 5yr p-value → `eg`, `eg_direction`, `eg_reverse`, `eg_reverse_direction`
4. `eg_5yr_passes = primary["is_cointegrated"]`

**2yr phase:**
5. `run_engle_granger` in both directions on 2yr data → `eg_ab_2yr`, `eg_ba_2yr`
   (fresh OLS — α₂, β₂ are independent of the 5yr values)
6. Primary = lower 2yr p-value → `eg_2yr`, `eg_direction_2yr`, etc.
7. `eg_2yr_passes = primary_2yr["is_cointegrated"]`

**Verdict:**
8. `pair_passes = eg_5yr_passes and eg_2yr_passes`

**Quarterly phase (display only):**
9. Fetch 1yr; split into 4 equal windows (~63 obs)
10. Each quarter: `run_engle_granger` in both directions (fresh OLS for that quarter)
11. Quarter dict: `{ label, start_date, end_date, n_obs, eg_ab, eg_ba, primary_p, primary_direction, passes }`
12. `quarters_passing` = count of `passes == True`

**Stability diagnostics:**
13. `compute_rolling_beta(prim_y_5yr, prim_x_5yr)` → `rolling_beta`

**Return dict keys:**
- `sym_a, sym_b, adf_a, adf_b`
- `eg, eg_direction, eg_reverse, eg_reverse_direction, eg_5yr_passes`
- `eg_ab_5yr, eg_ba_5yr` — raw A→B and B→A 5yr results (for β comparison)
- `eg_ab_2yr, eg_ba_2yr` — raw A→B and B→A 2yr results (for β comparison)
- `eg_2yr, eg_direction_2yr, eg_reverse_2yr, eg_reverse_direction_2yr, eg_2yr_passes`
- `pair_passes`
- `quarters` (list), `quarters_passing`
- `rolling_beta, rolling_beta_direction, rolling_beta_window`

---

## Dashboard layout (`app/streamlit_app.py`)

### Section 1 — I(1) prerequisite banner (5yr log prices)
Compact status using `adf_a["is_i1"]` and `adf_b["is_i1"]`. Warnings if not I(1).

### Section 2 — EG spread charts

**`_render_eg_pair(eg_res, eg_dir, is_primary, period_label, line_color)`**
- Header: `★ Primary direction — DEP (Y) regressed on INDEP (X)`
- 2 metrics: α (intercept), β (elasticity)
- Spread chart: log-price residuals, ±1σ, mean; y-axis = "Log-Price Spread"
- 5 metrics: test stat, p-value, crit 1%/5%/10%
- Verdict banner

**Past 5 Years** — heading notes that α and β come from 5yr OLS:
- Primary direction (blue `#2196F3`)
- Reverse direction (purple `#9C27B0`)

**Past 2 Years — Fresh OLS on 2yr data** heading:
- Primary direction (teal `#00897B`)
- Reverse direction (orange `#F57C00`)

**β Comparison block** (after 2yr charts, 4 metric columns):
- `β  A(Y)→B(X)  5yr` | `β  A(Y)→B(X)  2yr` (delta vs 5yr) | same for B→A
- Delta = 2yr β − 5yr β; large shift suggests structural drift

### Section 3 — Quarterly display (reference only, fresh per-quarter OLS)
Each quarter card:
- ★ on lower p-value direction
- Both directions show `TICKER (Y) on TICKER (X)  β=X.XXX` + p-value
- Pass/fail badge based on ★ direction p < 0.05

Caption notes: fresh per-quarter OLS; results are reference only.

### Section 4 — Final Verdict
- Two-column: 5yr result | 2yr result
- PASS/FAIL banner
- Quarterly count as footnote

### Section 5 — Stability Diagnostics
Rolling β chart on 5yr history (primary direction):
- Line: rolling β | Red dashed: fixed 5yr β | Gray dotted: ±1σ of rolling β
- 4 summary metrics: Fixed 5yr β | Rolling mean | Rolling std | Range

---

## Key design decisions

- **Independent OLS per window**: Each window (5yr, 2yr, each quarter) estimates its own
  α and β from its own data. This lets the β comparison (5yr vs 2yr) directly answer
  "has the hedge ratio drifted in the recent 2 years vs the 5-year baseline?"

- **2yr as confirmation with independent β**: If the 2yr OLS independently finds
  cointegration, it means the shorter-horizon data supports the same relationship — stronger
  evidence than forcing 5yr params onto 2yr data.

- **β comparison on both directions**: Showing both A→B and B→A β changes gives a
  complete picture of how the relationship has evolved regardless of which direction is primary.

- **Quarterly shows β per card**: Each quarter's fresh β is shown next to the p-value
  (e.g. `β=0.540`), making the short-term β volatility visible without needing a separate chart.

- **Rolling β uses 5yr primary direction**: The stability diagnostics chart uses the 5yr
  primary direction consistently so the rolling β line is directly comparable to the fixed 5yr β.

- **log(price) + AIC throughout**: All adfuller calls use autolag='AIC'. Regression is
  log-log, giving β an elasticity interpretation standard in financial econometrics.
