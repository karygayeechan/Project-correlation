# Cointegration Test — Implementation Plan

## Goal
Add a "Cointegration Test" tab to the Streamlit dashboard (between Rolling Correlation
and Price & Returns) that runs two statistical tests on any chosen pair of stocks using
5-year price data, following the exact procedure in "Cointegration test instruction".

---

## Step 1 — Install dependency
`statsmodels` is required for `adfuller()` and OLS regression.
Command: `uv pip install statsmodels`

---

## Step 2 — Create `Cointegration test/cointegration.py`
Pure computation module. No Streamlit imports.

### Functions
```
fetch_prices(sym_a, sym_b) -> (pd.Series, pd.Series)
```
- Pulls 5-year adj_close from DB for both symbols via psycopg2 (same pattern as app/db.py)
- Returns two aligned price Series indexed by date

```
run_adf(series, label) -> dict
```
- Runs `statsmodels.tsa.stattools.adfuller()` on the series
- Returns: { label, stat, p_value, critical_values, is_stationary (p<0.05), verdict ("✓" or "?") }
- p < 0.05 → stationary → verdict = "?"  (unexpected for raw prices)
- p > 0.05 → non-stationary → verdict = "✓"  (expected, good for cointegration)

```
run_engle_granger(series_a, series_b) -> dict
```
- Step 1: OLS regress A on B (with intercept) → extract α (intercept) and β (hedge ratio)
- Step 2: Compute residuals = A − (α + β·B)  i.e. ϵt = At − (α + β·Bt)
- Step 3: Run adfuller() on residuals
- Returns: { alpha, beta, residuals (Series), stat, p_value, critical_values,
             is_cointegrated (p<0.05), verdict ("✓" or "✗") }

```
run_all(sym_a, sym_b) -> dict
```
- Calls all three functions above
- Evaluates final pass condition:
    1. ADF on A: p > 0.05  ✓
    2. ADF on B: p > 0.05  ✓
    3. Engle-Granger residual ADF: p < 0.05  ✓
- Returns full results dict including `pair_passes: bool`

---

## Step 3 — Create `Cointegration test/conclusions.py`
Maps numeric results to plain-English sentences displayed in the UI.

```
adf_conclusion(is_stationary) -> str
```
- True  → "Series is stationary — not ideal for cointegration testing (prices usually aren't)."
- False → "Series is non-stationary — expected for stock prices, required for cointegration."

```
eg_conclusion(is_cointegrated) -> str
```
- True  → "Residuals are stationary: the pair is cointegrated. A stable long-run relationship exists."
- False → "Residuals are non-stationary: the pair is NOT cointegrated. No stable spread to trade."

```
pair_conclusion(pair_passes) -> str
```
- True  → "PASS — pair meets all cointegration criteria. Suitable for pairs trading."
- False → "FAIL — pair does not meet all criteria. Not suitable for pairs trading."

---

## Step 4 — Add tab to `app/streamlit_app.py`

### 4a — Tab slot
Insert `tab_coint` between `tab_roll` and `tab_price` in the `st.tabs()` call.
Label: `"Cointegration Test"`

### 4b — Tab content (with tab_coint)
Layout:
1. Ticker selectors — Stock A (default NVDA) and Stock B (default TSM), dropdowns from DB tickers
2. "Run Test" button triggers computation (not auto-run on load)
3. On run:

   **Section 1 — Individual ADF Tests**
   Two columns, one per stock:
   - Subheader: "ADF Test: {symbol}"
   - Metrics: Test Statistic, P-Value, Critical Values (1% / 5% / 10%)
   - Verdict badge: st.success (✓ non-stationary) or st.warning (? stationary)
   - Conclusion sentence from conclusions.py

   **Section 2 — Engle-Granger Test**
   - Show hedge ratio β
   - Spread chart (residuals over time) with mean line and ±1σ bands
   - Metrics: ADF Stat on spread, P-Value, Critical Values
   - Verdict badge: st.success (✓ cointegrated) or st.error (✗ not cointegrated)
   - Conclusion sentence from conclusions.py

   **Section 3 — Final Verdict**
   - Checklist of all 4 criteria with ✓/✗ per criterion
   - st.success (green banner) if pair_passes, st.error (red banner) if not
   - Conclusion sentence from conclusions.py

---

## File structure after implementation
```
Cointegration test/
  Cointegration test instruction   # original spec (already exists)
  PLAN.md                          # this file
  cointegration.py                 # computation logic
  conclusions.py                   # verdict text
```
`app/streamlit_app.py`             # modified to add the new tab
