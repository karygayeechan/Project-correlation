# Cointegration Test — Implementation Plan

## Goal
Add a "Cointegration Test" tab to the Streamlit dashboard that runs cointegration tests on any chosen pair of stocks. Three data windows are used, each serving a different purpose:

| Window | Data period | Used for |
|---|---|---|
| **5-year** | Past 5 years (365 × 5 calendar days) | ADF prerequisite banner + EG spread charts |
| **2-year** | Past 2 years (365 × 2 calendar days) | Additional EG spread charts |
| **1-year** | Past 1 year (365 calendar days) | Quarterly p-value table (4 windows × ~63 trading days) |

All data is fetched live from the DB (`stock_prices` table) via psycopg2 with `end = date.today()` — results always reflect the most recent available prices.

---

## Module: `Cointegration test/cointegration.py`

Pure computation module — no Streamlit imports.

### `fetch_prices(sym_a, sym_b, days=365) -> (pd.Series, pd.Series)`
- Fetches `adj_close` from DB for both symbols over the past `days` calendar days ending today
- Returns two aligned price Series indexed by date
- Called three times in `run_all`: with `days=365*5`, `days=365*2`, and `days=365`

### `run_adf(series, label) -> dict`
- Runs `adfuller()` on the series
- Returns: `{ label, stat, p_value, critical_values, is_stationary (p<0.05), verdict }`
- p < 0.05 → stationary → `verdict = "?"` (unexpected for raw prices)
- p > 0.05 → non-stationary → `verdict = "✓"` (expected; required for cointegration)
- **Data used: 5-year series**

### `run_engle_granger(series_a, series_b) -> dict`
- OLS: regress A on B (with intercept) → α (intercept), β (hedge ratio)
- Residuals = A − (α + β·B)
- ADF on residuals → `stat`, `p_value`, `critical_values`, `is_cointegrated (p<0.05)`, `verdict`
- Called 12 times per `run_all`: twice on 5yr data, twice on 2yr data, twice per quarter × 4 quarters = 8

### `run_all(sym_a, sym_b) -> dict`
Orchestrates all three windows and returns a single result dict:

**5-year fetch** (`days=365*5`):
- `adf_a`, `adf_b` — ADF on each series
- `eg` / `eg_direction` — primary EG direction (lower p-value of A→B vs B→A)
- `eg_reverse` / `eg_reverse_direction` — reverse EG direction
- Spread residuals span ~1,255 trading days (less for tickers with shorter history, e.g. ARM IPO Sep 2023)

**2-year fetch** (`days=365*2`):
- `eg_2yr` / `eg_direction_2yr` — primary EG direction on 2yr data
- `eg_reverse_2yr` / `eg_reverse_direction_2yr` — reverse EG direction on 2yr data
- Spread residuals span ~502 trading days

**1-year fetch** (`days=365`):
- Split into **4 equal quarterly windows** (Q1 = oldest, Q4 = most recent, ~63 trading days each)
- Each quarter: `eg_ab` (A→B), `eg_ba` (B→A), `primary_p` (lower of the two), `primary_direction`, `passes (p < 0.05)`
- `quarters_passing` — integer count (0–4)
- `pair_passes` — True only when all 4 quarters pass

---

## Module: `Cointegration test/conclusions.py`

### `adf_conclusion(is_stationary) -> str`
- True  → "Series is stationary — not ideal for cointegration testing."
- False → "Series is non-stationary — expected for stock prices, required for cointegration."

### `eg_conclusion(is_cointegrated) -> str`
- True  → "Residuals are stationary: the pair is cointegrated."
- False → "Residuals are non-stationary: the pair is NOT cointegrated."

### `pair_conclusion(pair_passes) -> str`
- True  → "PASS — pair meets all cointegration criteria."
- False → "FAIL — pair does not meet all criteria."

---

## Dashboard layout (`app/streamlit_app.py`)

### Controls
- Stock A / Stock B dropdowns (all DB tickers; default ARM / TSM)
- "Run Cointegration Test" button (no auto-run on load)

### Section 1 — ADF prerequisite banner (5-year data)
- **Not** shown as a full metrics table — displayed as a compact status banner
- Both non-stationary (normal case): green success — `"✓ TICKER_A (p=X) and TICKER_B (p=Y) are both non-stationary over the past 5 years — proceeding to Engle-Granger"`
- Any series stationary (unusual): amber warning alert naming the ticker + p-value; then info banner for the non-stationary one; then `"Proceeding to Engle-Granger test."`

### Section 2 — Engle-Granger spread charts

**Past 5 Years** (heading)
- **★ Primary direction** — `TICKER_A regressed on TICKER_B` (or B on A if that is the lower p-value)
  - Chart title: `Spread (5yr): TICKER_A→TICKER_B  [Mon YYYY → Mon YYYY]`
  - Spread chart with ±1σ bands and mean line
  - Metrics: α, β, ADF stat, p-value, critical values (1%/5%/10%), cointegrated ✓/✗
- **Reverse direction** — opposite regression
  - Chart title: `Spread (5yr): TICKER_B→TICKER_A  [Mon YYYY → Mon YYYY]`
  - Same metric layout

`---` separator

**Past 2 Years** (heading)
- **★ Primary direction** (2-year, independently determined)
  - Chart title: `Spread (2yr): TICKER_A→TICKER_B  [Mon YYYY → Mon YYYY]`
- **Reverse direction** (2-year)
  - Chart title: `Spread (2yr): TICKER_B→TICKER_A  [Mon YYYY → Mon YYYY]`

Primary direction is determined independently per window — which direction has the lower p-value can differ between 5yr and 2yr.

### Section 3 — Quarterly p-values (1-year data)
- Caption states: "★ marks the primary direction (lower p-value), which determines pass/fail. Pass condition: p < 0.05."
- Four side-by-side cards (Q1 = oldest, Q4 = most recent):
  - Date range (e.g. `Jun 17 2025 → Sep 16 2025`)
  - Row 1: `★ TICKER_A regressed on TICKER_B` + p-value metric (★ on whichever is primary)
  - Row 2: `TICKER_B regressed on TICKER_A` + p-value metric
  - Pass/fail badge: `Cointegrated ✓` (green) or `Not cointegrated ✗` (red)
- Below cards: `X/4 quarters passed` summary + final PASS/FAIL banner

---

## File structure
```
Cointegration test/
  Cointegration test instruction   # original spec (updated to reflect data periods)
  PLAN.md                          # this file
  cointegration.py                 # computation logic
  conclusions.py                   # verdict text
```
`app/streamlit_app.py`             # modified to add the tab and all sections above
