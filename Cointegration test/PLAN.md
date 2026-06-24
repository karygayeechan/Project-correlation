# Cointegration Test — Implementation Plan

## Goal
Cointegration test tab in Streamlit. Three data windows for EG tests plus stability diagnostics
and structural break analysis. All computations use log(price); regression version 4 throughout.

| Window | Data period | OLS | Used for |
|---|---|---|---|
| **5-year** | 365 × 5 cal days | Fresh on 5yr data | I(1) check; EG test (verdict); rolling diagnostics |
| **2-year** | 365 × 2 cal days | Fresh on 2yr data | EG confirmation (verdict); β comparison vs 5yr |
| **1-year** | 365 cal days | Fresh per ~63-day quarter | Quarterly display only (not verdict) |

Verdict: **PASS under Path 1 or Path 2.**
- **Path 1 (standard):** 5yr primary p < 0.05 AND 2yr primary p < 0.05 AND both primaries run in the same regression direction. Opposing directions (e.g. 5yr A→B, 2yr B→A) disqualify the pair.
- **Path 2 (post-break):** Post-break EG re-test passes (either direction p < 0.05) AND ZA break date is > 2 years before today.

---

## File structure
```
Cointegration test/
  Cointegration test instruction   # full spec (this PLAN summarises implementation)
  PLAN.md                          # this file
  cointegration.py                 # all computation logic
  conclusions.py                   # plain-English verdict strings
  break_commentary.py              # AI break-period commentary agent
app/streamlit_app.py               # tab rendering (imports all modules above)
```

---

## Module: `cointegration.py`

### `fetch_prices(sym_a, sym_b, days) -> (Series, Series)`
Fetches `adj_close` from DB. Log transform applied downstream in each function.

### `run_adf(series, label) -> dict`
Tests log(price) for I(1). `autolag='AIC'` throughout.
- Level: `adfuller(log_price, autolag='AIC')`
- Diff:  `adfuller(log_price.diff().dropna(), autolag='AIC')`
- `is_i1 = (level p > 0.05) AND (diff p < 0.05)`
- Returns: `label, stat, p_value, critical_values, is_stationary, verdict, diff_p_value, is_diff_stationary, is_i1`

### `run_engle_granger(series_a, series_b) -> dict`
Version 4: `log(A) = α + β·log(B) + ε` (OLS with constant on log prices).
`adfuller(residuals, autolag='AIC')` on OLS residuals.
Returns: `alpha, beta, residuals, stat, p_value, critical_values, is_cointegrated, verdict`

### `compute_rolling_beta(series_y, series_x, window=252) -> pd.Series`
Vectorized closed-form rolling OLS β. Stability diagnostics only — not part of EG verdict.

### `compute_rolling_eg_pvalue(series_y, series_x, window=252) -> pd.Series`
Rolling 252-day EG p-value: at each date, runs OLS + `adfuller(residuals)` on trailing window.
Uses plain `adfuller` (not MacKinnon 2010 table) — for visualisation only, not verdict.

### `detect_structural_break(residuals) -> dict | None`
Zivot-Andrews test on the 5yr primary OLS residuals.
`zivot_andrews(residuals, regression='c', autolag='t-stat')`
Returns: `stat, pvalue, critical_values, break_date, breakpoint_idx, is_break (p < 0.05)`
The detected date is the sharpest inflection point in the spread, not the point of maximum
divergence. ZA selects the date that, when a level shift is allowed there, produces the most
negative ADF statistic across all candidate dates.

### `identify_break_periods(rolling_pvalue, threshold=0.05, min_days=30) -> list[dict]`
Scans the rolling EG p-value series for contiguous stretches above threshold.
Filters to ≥ min_days to remove noise. Returns list sorted longest-first.
Each dict: `{ start, end, days }`.

### `run_eg_post_break(series_a, series_b, break_date, sym_a, sym_b) -> dict | None`
Runs full EG in BOTH directions on data from break_date → today.
Re-estimates α, β independently per direction from the post-break window.
Primary direction = lower post-break p-value.
Returns None if < 60 observations remain after break.
Returns dict with keys: `primary, reverse, primary_direction, reverse_direction, n_obs, window_start, window_end`

### `run_all(sym_a, sym_b) -> dict`

**5yr phase:**
1. `run_adf` on each log-price series → `adf_a`, `adf_b`
2. `run_engle_granger` both directions on 5yr → `eg_ab_5yr`, `eg_ba_5yr`
3. Primary = lower 5yr p → `eg`, `eg_direction`, `eg_reverse`, `eg_reverse_direction`
4. `eg_5yr_passes = primary["is_cointegrated"]`

**2yr phase:**
5. `run_engle_granger` both directions on 2yr → `eg_ab_2yr`, `eg_ba_2yr` (fresh OLS)
6. Primary = lower 2yr p → `eg_2yr`, `eg_direction_2yr`, etc.
7. `eg_2yr_passes = primary_2yr["is_cointegrated"]`

**Direction check + Path 1:**
8. `direction_match = (eg_direction == eg_direction_2yr)`
9. `path1_passes = eg_5yr_passes and eg_2yr_passes and direction_match`

**Quarterly (display only):**
9. Fetch 1yr; split into 4 equal windows (~63 obs)
10. `run_engle_granger` both directions per quarter (fresh OLS)
11. Quarter dict: `{ label, start_date, end_date, n_obs, eg_ab, eg_ba, primary_p, primary_direction, passes }`
12. `quarters_passing` = count of `passes == True`

**Stability diagnostics:**
13. `compute_rolling_beta(prim_y_5yr, prim_x_5yr)` → `rolling_beta`
14. `compute_rolling_eg_pvalue(prim_y_5yr, prim_x_5yr)` → `rolling_eg_pvalue`

**Structural break analysis:**
15. `detect_structural_break(eg_primary["residuals"])` → `structural_break`
16. `identify_break_periods(rolling_eg_pvalue)` → `break_periods` (≥ 30 days, sorted longest-first)
17. If structural_break is not None: `run_eg_post_break(series_a_5yr, series_b_5yr, sb["break_date"], sym_a, sym_b)` → `eg_post_break`
    Start date = ZA break date (sharpest inflection point); both directions tested.

**Path 2 + final verdict:**
18. `post_break_passes = pb["primary"]["is_cointegrated"] or pb["reverse"]["is_cointegrated"]`
19. `post_break_over_2yr = (date.today() - sb["break_date"].date()).days > 730`
20. `path2_passes = post_break_passes and post_break_over_2yr`
21. `pair_passes = path1_passes or path2_passes`

**Return dict keys:**
- `sym_a, sym_b, adf_a, adf_b`
- `eg, eg_direction, eg_reverse, eg_reverse_direction, eg_5yr_passes`
- `eg_ab_5yr, eg_ba_5yr` (for β comparison)
- `eg_ab_2yr, eg_ba_2yr` (for β comparison)
- `eg_2yr, eg_direction_2yr, eg_reverse_2yr, eg_reverse_direction_2yr, eg_2yr_passes`
- `direction_match, path1_passes`
- `post_break_passes, post_break_over_2yr, path2_passes`
- `pair_passes`
- `quarters` (list), `quarters_passing`
- `rolling_beta, rolling_beta_direction, rolling_beta_window`
- `rolling_eg_pvalue`
- `structural_break, break_periods, post_break_start_date, eg_post_break`

---

## Module: `break_commentary.py`

### Purpose
On-demand AI commentary (~200 words) explaining what real-world events caused the main break
period. Grounded strictly in web search results — every claim must cite a specific article.

### Agent design

**Entry point:** `generate_break_commentary(sym_a, sym_b, break_start, break_end, break_days, za_break_date=None) -> str`

**Model:** `claude-opus-4-7` with `web_search_20250305` tool enabled.

**System prompt (strict anti-hallucination rules):**
```
You are a financial analyst. Write approximately 200 words.
STRICT ANTI-HALLUCINATION RULES — NO EXCEPTIONS:
1. You may ONLY state facts directly supported by an article in the search results you received.
   Do not use any background knowledge.
2. After every sentence or claim, include a citation in this exact format:
   ["Article Title", Source Name, Date]
3. If the search results do not contain enough evidence to explain the break, say so
   explicitly — do not speculate.
4. Write in plain prose. No bullet points, no headers.
```

**User prompt asks Claude to search for:**
1. `{sym_a} {sym_b} stock performance {years}`
2. `{sym_a} {sym_b} earnings results balance sheet {years}`
3. `Federal Reserve interest rate hikes impact bank stocks {years}`
4. `Banking sector crisis stress {years}`

**Agentic loop:**
```
while not end_turn:
    response = client.messages.create(model, tools=[web_search_20250305], messages)
    if stop_reason == "end_turn": return text
    for each tool_use block (web_search):
        query = block.input["query"]
        content = _search_google_news(query)   # Google News RSS via requests
        append tool_result with content
    append tool_result message and continue
```

**`_search_google_news(query, max_results=6) -> str`**
Fetches `https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en`.
Parses RSS XML; extracts title, source, date, URL, snippet per item.
Returns formatted text block passed as `tool_result` content to Claude.

**Citation format required in output:** `["Article Title", Source Name, Date]`
Only named reliable sources are permitted (Reuters, Bloomberg, FT, WSJ, CNBC, etc.).

**Session state caching:** result stored under `st.session_state[f"break_commentary_{A}_{B}"]`;
survives button reruns without regenerating.

---

## Dashboard layout (`app/streamlit_app.py`)

### Session state caching
Cointegration results (`cr`) stored in `st.session_state["coint_result"]` keyed by
`f"coint_result_{sym_a}_{sym_b}"`. On reruns triggered by other buttons (e.g. commentary),
`cr` is restored from session state so all content remains visible.

### Section 1 — I(1) prerequisite banner
Compact status from `adf_a["is_i1"]` / `adf_b["is_i1"]`. Warnings if not I(1).

### Section 2 — EG spread charts

`_render_eg_pair(eg_res, eg_dir, is_primary, period_label, line_color)`:
- Header: `★ Primary — DEP (Y) regressed on INDEP (X)`
- Metrics: α, β (elasticity); spread chart with ±1σ; 5 stat columns; verdict banner

**5yr (blue/purple):** heading notes α and β come from 5yr OLS.
**2yr (teal/orange):** fresh 2yr OLS; followed by β comparison block (4 metric columns with deltas).

### Section 3 — Quarterly display
Cards: ★ on lower-p direction; both directions show `TICKER (Y) on TICKER (X)  β=X.XXX` + p.

### Section 4 — Final Verdict
Two-column 5yr/2yr results → PASS/FAIL banner → quarterly footnote.

### Section 5 — Stability Diagnostics
Rolling β chart (line + 5yr ref line + ±1σ bands) + 4 summary metrics.

### Section 6 — Structural Break Analysis
1. **Rolling EG p-value chart** — p over 5yr history, 0.05 threshold (red dashed), ZA break
   date (orange dotted vertical). Caption explains ZA date interpretation.
2. **Break periods list** — contiguous stretches of rolling p > 0.05, ≥ 30 days, sorted
   chronologically; longest marked "main break".
3. **Break period commentary** — "Generate Commentary (AI)" button; shows `st.info` block
   with ~200-word cited analysis; cached in session_state.
4. **ZA test result** — amber warning (formally detected) or info (candidate only).
5. **Post-break EG re-test** — both directions rendered with `_render_pb_direction()`;
   overall banner if either direction passes.

---

## Key design decisions

- **Two-path verdict**: Path 1 requires matching regression direction across 5yr and 2yr tests —
  a pair that passes A→B over 5yr but B→A over 2yr has flipped its causal structure, which is
  economically incoherent for pairs trading. Path 2 allows a PASS via post-break cointegration
  if the new regime has been running for more than 2 years, providing enough history to trust
  the post-break estimate.
- **Version 4 regression throughout**: log-log with constant gives β an elasticity
  interpretation; constant absorbs price-level scale differences; autolag='AIC' for lags.
- **Independent OLS per window**: each window estimates its own α, β. β comparison (5yr vs 2yr)
  directly answers "has the hedge ratio drifted in the recent period?"
- **Verdict from 5yr AND 2yr**: the 5yr test anchors the long-run relationship; the 2yr test
  confirms it holds in the recent regime. Requiring both prevents passing on stale history alone.
- **Quarterly is display-only**: ~63 obs windows have low power for cointegration tests; they're
  shown for reference to see short-term stability, not for the verdict.
- **Rolling EG p-value for break period identification**: identifies start and end of break
  periods more reliably than a single break-point test; 30-day minimum filters noise.
- **ZA date for post-break window start**: ZA identifies the sharpest inflection point in the
  spread — the moment the relationship changed direction most abruptly. This is the natural
  start of the "new regime" regardless of whether ZA formally rejects the null.
- **Post-break in both directions**: EG asymmetry means the post-break primary direction may
  differ from the full-period primary; testing both avoids missing a passing direction.
- **Break commentary grounded in web search**: every claim must cite a specific article returned
  by Google News RSS search. Claude is prohibited from using background knowledge, preventing
  hallucination of source details. The agentic loop gives Claude up to 10 search iterations.
- **Session state caching of `cr`**: prevents all cointegration results from disappearing when
  the "Generate Commentary" button triggers a Streamlit rerun.
