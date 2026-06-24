# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Stock correlation analysis pipeline: fetches stock prices via yfinance (NVDA, GOOGL, AVGO, ARM, TSM, JPM, BAC), computes pairwise Pearson correlations (6m/12m/24m), stores results in PostgreSQL, visualizes via Streamlit, monitors macro regime indicators (10Y Treasury yield, TIPS real yield, Nasdaq-100 breadth, VIX, SMH/QQQ relative strength) with a 10-rule alert engine, generates AI-written regime commentary via the Claude API, runs full cointegration analysis (log-log EG across 5yr/2yr/quarterly windows, structural break detection, post-break re-test, AI break commentary grounded in web search), executes pairs-trading signals with a quarterly-fixed hedge ratio, and compares quarterly fundamentals for any two stocks with a 3-layer alert engine and AI-written fundamental briefing grounded exclusively in yfinance data.

## Environment Setup

### PostgreSQL 16 (macOS)
```bash
brew install postgresql@16
brew services start postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"  # add to ~/.zshrc
psql -d postgres -f db/schema.sql
```

### Python (3.11.9, uv recommended)
```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv statsmodels fredapi
```

### Required env vars
Create a `.env` at the project root (loaded via `python-dotenv`):
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=<your_pg_user>
DB_PASSWORD=<your_pg_password>
ANTHROPIC_API_KEY=<your_api_key>   # optional — needed for AI Regime Commentary button
FRED_API_KEY=<free_key>            # free from fred.stlouisfed.org — needed for TIPS real yield
```

## Commands

| Task | Command |
|------|---------|
| Apply schema | `psql -d postgres -f db/schema.sql` |
| Run full ETL | `python3 etl/load.py` |
| Launch API backend | `uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload` |
| Launch dashboard | `streamlit run app/streamlit_app.py` |
| Smoke-test extract | `python etl/extract.py` |
| Smoke-test transform | `python etl/transform.py` |

## What's Built

**ETL pipeline:**
- `etl/extract.py` — fetches 5 years OHLCV for NVDA, GOOGL, AVGO, ARM, TSM, JPM, BAC via yfinance
- `etl/transform.py` — reshapes wide → long; `compute_correlations()` produces **6m/12m/24m** Pearson pairs (1m removed)
- `etl/load.py` — inserts companies, prices, upserts correlations, writes etl_log (no longer archives snapshots or calls commentary agent)

**Regime Detection Agent (`Regime detection agent/`):**
- `data_collector.py` — fetches 5 macro indicators via yfinance + FRED; entry point `fetch_indicators(lookback_days=365)` returns a tidy DataFrame with columns: `treasury_10y`, `tips_10y`, `nasdaq_breadth`, `vix`, `smh_qqq_ratio`, `smh_qqq_zscore`
- `regime_alerts.py` — evaluates 10 alert rules across 5 indicator families; entry point `detect_alerts(df)` returns a list of alert dicts with `triggered`, `recently_crossed`, `severity`, `message`
- `commentary.py` — on-demand agent: fetches indicators, evaluates alerts, calls Claude to write a ~100-word macro regime briefing, stores in `correlation_alerts`
- `PLAN.md` — indicator sources, all alert rules with thresholds, architecture decisions

**API:**
- `api/main.py` — FastAPI backend with 11+ endpoints including `/prices/latest-date`; `app/db.py` is the query layer. Period parameter accepts `6m`, `12m`, `24m`, `60m`.

**Fundamental Comparison Agent (`fundamental comparison agent/`):**
- `fundamental_data.py` — fetches up to 8 quarters of quarterly income statement, balance sheet, and cash flow for any ticker via yfinance; normalizes to $B; computes 11 derived quality ratios (DSO, Inventory Days, Gross/Op/Net Margins, D/E, Current Ratio, OCF/NI, Accrual Ratio, FCF Margin, R&D % Revenue); entry point `fetch_fundamentals(symbol)` returns a dict with DataFrames + metadata
- `fundamental_alerts.py` — 3-layer alert engine; entry point `detect_fundamental_alerts(data_a, data_b)` returns alerts sorted Critical → Warning → Info:
  - **Layer 1 (Pair relative-shift)**: tracks how the A-vs-B differential changed QoQ (reversal / A-gaining / A-losing) across 9 metrics; reversals on Revenue/NI/FCF escalated to Critical
  - **Layer 2a (Individual trend)**: 11 QoQ/YoY checks per stock (revenue drop, NI swing, margin contraction, FCF decline, cash drop, debt surge, EPS decline)
  - **Layer 2b (Earnings quality)**: 17 pattern and ratio checks per stock covering all 7 named concern patterns (e.g. "Aggressive revenue recognition", "Financial engineering", "Dependence on external funding") — alert messages include the "Potential Concern" label as a second clause
- `fundamental_commentary.py` — on-demand Claude agent; **anti-hallucination hardcoded** via system prompt: Claude may only use figures in the supplied KEY METRICS and ALERTS blocks; no external data, news, or estimates permitted; entry point `generate_fundamental_commentary(data_a, data_b, alerts)` returns a ~200-word briefing string (no DB writes)
- `PLAN.md` — full design spec: metrics tracked, alert rules + thresholds, pattern → concern table, QoQ convention, anti-hallucination design, architecture decisions

**Dashboard:**
- `app/streamlit_app.py` — 7-tab Streamlit dashboard: Correlation (sub-tabs: Heatmap / Rolling / Network Graph), Cointegration, Trading Signals (+ hypothetical 5-year PnL), Backtest (4yr/1yr), Regime Alerts, **Fundamental Comparison**, Manage Tickers
- **Auto-ETL on startup**: on first load of each browser session, checks `/prices/latest-date`; if data is stale on a weekday, runs ETL for all DB tickers automatically before rendering tabs
- `app/api_client.py` — HTTP client so Streamlit never touches the DB directly

**Correlation tab (sub-tabs):**
- **Heatmap** — period radio: **6m / 12m / 24m** (default 24m); ranked pairs table shows all three period columns (24m r → 12m r → 6m r), sorted by selected period
- **Rolling** — fixed **90-day rolling window** displayed over a **5-year span** (always pinned to `date.today()` regardless of sidebar); title reads "Rolling Correlation 5yr (90d window)"
- **Network Graph** — period radio: **24m / 60m** (default 24m); minimum |r| threshold default **0.65**

**Cointegration (`Cointegration test/`):**
- `cointegration.py` — full pipeline; all computations use log(price); regression version 4 (OLS with constant on log prices) throughout:
  - **I(1) check** — level + diff ADF on log prices for each series; `is_i1 = (level p > 0.05) AND (diff p < 0.05)`
  - **5yr EG** — bidirectional OLS → `adfuller(residuals, autolag='AIC')`; verdict component 1
  - **2yr EG** — fresh independent OLS; β comparison vs 5yr; verdict component 2
  - **Quarterly** — fresh per-quarter OLS (~63 obs); display only, not verdict
  - **Verdict** — PASS under Path 1 or Path 2: Path 1 = 5yr p < 0.05 AND 2yr p < 0.05 AND both primaries in the same regression direction; Path 2 = post-break EG re-test passes AND ZA break date > 2 years ago
  - **Stability diagnostics** — `compute_rolling_beta()` (rolling 252d OLS β) and `compute_rolling_eg_pvalue()` (rolling 252d EG p-value), both on 5yr primary direction
  - **Structural break analysis** — `detect_structural_break()` runs Zivot-Andrews on 5yr residuals to find the sharpest inflection point; `identify_break_periods()` scans rolling EG p-value for stretches > 0.05 ≥ 30 days; `run_eg_post_break()` re-runs EG in both directions from the ZA break date onwards
- `break_commentary.py` — on-demand AI agent: agentic loop with `web_search_20250305` tool; Claude issues search queries; client executes via Google News RSS (`_search_google_news()`); strict anti-hallucination system prompt requires every claim to cite a specific retrieved article in format `["Title", Source, Date]`; ~200 words; result cached in `st.session_state`
- `conclusions.py` — plain-English verdict strings
- Tab layout: I(1) banner → 5yr EG charts → 2yr EG charts + β comparison → quarterly cards → Final Verdict → Stability Diagnostics (rolling β + rolling EG p-value) → Structural Break Analysis (break periods, ZA result, AI commentary, post-break re-test)

**Trading Signals (`Trading signals/`):**
- `trading_signals.py` — **quarterly-fixed** hedge ratio β: estimated from a trailing 1-year (252-day) OLS at each calendar-quarter boundary, held constant for the full quarter. Z-score uses a 60–120 day rolling window (default 90). Generates LONG/SHORT/EXIT/HOLD signals; `position_B = −β_q × position_A`. Includes `quarter` column in output. Default pair: ARM/TSM.
- Hypothetical 5-year PnL (cumulative, daily bars, monthly breakdown) is rendered at the bottom of the Trading Signals tab
- Constants: `BETA_WINDOW = 252` (β estimation), `WINDOW = 90` (z-score default)

**Backtest (`Backtest/`):**
- `backtest.py` — 4y/1y train-test split; runs `compute_rolling_signals` on full 5-year history (4-year warm-up ensures all test-period quarters have a calibrated β), slices the last 1 year for evaluation; in-memory only; computes performance, trading activity, risk, stability, and scalability metrics. Default pair: ARM/TSM.
- `PLAN.md` — implementation notes and design decisions
- `Backtest instruction` — original specification for the backtest tab

**Database:**
- 7 tables: `companies`, `company_details`, `stock_prices`, `correlations`, `correlation_history` (kept in schema, no longer written to by ETL), `correlation_alerts` (stores regime commentary), `etl_log`
- `correlations.period` constraint: `CHECK (period IN ('6m', '12m', '24m'))` — 1m removed

## Architecture Decisions

- **Surrogate SERIAL keys** for companies — ticker symbols can change (FB→META); integer PKs are stable
- **Precomputed correlations** stored in `correlations` table — avoids recomputing on every Streamlit render; periods are 6m/12m/24m
- **Idempotent ETL** — all inserts use `ON CONFLICT DO NOTHING`; safe to re-run
- **Auto-ETL on Streamlit startup** — `st.session_state["etl_auto_refreshed"]` ensures the ETL runs once per browser session on weekdays if `latest price date < today`; uses `/prices/latest-date` endpoint for the staleness check; never blocks the dashboard if ETL fails
- **Regime commentary is on-demand, not ETL-triggered** — fetching 100+ tickers for NDX breadth takes ~30–60s; running that on every ETL would be prohibitive. Commentary is generated when the user clicks the button in Streamlit; one entry per calendar day is cached in `correlation_alerts`.
- **QQQ as trading-calendar reference** — `^TNX` and `^VIX` trade on CBOE holidays when equities are closed. Filtering `close[close["QQQ"].notna()]` removes those extra rows before any rolling window calculations, preventing NaN contamination of the 200DMA and z-score.
- **DMA_BUFFER = 400 calendar days** — the 252-day SMH/QQQ z-score needs 252 trading days of warmup. 400 calendar days ≈ 276 trading days, enough to keep the z-score valid across the entire 1-year display window.
- **State-based + crossing alerts** — each rule reports both current state (`triggered`) and whether it changed within the last 5 trading days (`recently_crossed`). This distinguishes a new actionable signal from a condition that has been true for weeks.
- **`correlation_history` retained in schema** — table is kept for backwards compatibility but ETL no longer writes to it; the regime agent does not use correlation snapshots.
- **Quarterly-fixed β, not daily rolling** — `trading_signals.py` and `backtest.py` estimate β from a trailing 1-year OLS once per calendar quarter (at the quarter boundary), then hold it fixed for the entire quarter. This eliminates daily β noise and negative-beta windows that could invert the hedge. `BETA_WINDOW = 252` trading days; z-score window is separately configurable (60–120 days).
- **Default pair is JPM/BAC** — set across all tabs (Rolling Correlation, Cointegration, Trading Signals, Backtest, Fundamental Comparison).
- **Cointegration uses log-log regression (version 4)** — `log(Y) = α + β·log(X) + ε` with a constant. β is an elasticity (% change in Y per 1% change in X), not a raw price ratio. The constant absorbs scale differences. `adfuller(residuals, autolag='AIC')` selects lag length automatically. All four versions were benchmarked on JPM/BAC; version 4 was chosen as the standard.
- **Cointegration verdict: two-path system** — Path 1 requires 5yr AND 2yr both pass (p < 0.05) AND both primaries run in the same regression direction; a direction flip (5yr: A→B, 2yr: B→A) is economically incoherent and disqualifies Path 1. Path 2 allows a PASS via the post-break EG re-test if the ZA break date was more than 2 years ago, ensuring enough history in the new regime to trust the estimate.
- **ZA break date is the sharpest inflection, not the worst point** — Zivot-Andrews picks the date where allowing a one-time level shift in the spread produces the most negative ADF statistic. This is the moment the spread changed direction most abruptly, typically at the onset of a structural event, not at the peak of divergence. The post-break re-test starts from this date.
- **Break commentary is web-search grounded** — `break_commentary.py` uses an agentic loop with the `web_search_20250305` tool. The client executes each search via Google News RSS and passes real article titles/sources/dates as `tool_result` content. Claude's system prompt prohibits using background knowledge; every claim must cite a specific returned article. This prevents hallucination of source details.
- **Cointegration results cached in session_state** — `cr` is stored under `st.session_state["coint_result"]` keyed by `f"coint_result_{sym_a}_{sym_b}"`. This prevents all tab content from disappearing when secondary buttons (e.g. "Generate Commentary") trigger Streamlit reruns. Cache is invalidated when the user switches the stock pair.
- **Fundamental comparison is in-memory only** — no DB writes; `fetch_fundamentals()` is cached 6 hours in Streamlit (`@st.cache_data(ttl=21600)`). Quarterly fundamentals don't change intraday so a 6-hour TTL is sufficient without hammering yfinance.
- **Fundamental commentary anti-hallucination** — `generate_fundamental_commentary()` passes a system prompt that explicitly prohibits Claude from using any information outside the supplied KEY METRICS and ALERTS blocks. This prevents the model from citing news, earnings calls, analyst estimates, or any external context not derived from the yfinance data in the prompt.
- **QoQ convention in Fundamental Comparison** — all DataFrames are sorted most-recent first (`iloc[0]` = current quarter, `iloc[1]` = prior quarter). Every QoQ alert and every delta arrow in the comparison tables compares those two positions. A "Q1 2026" row always shows the change vs Q4 2025.
- **Fundamental comparison fetches up to 8 quarters** — display tables show 4; the alert engine uses all available quarters for pattern checks (e.g. "cash declining for 3+ consecutive quarters") and YoY comparisons (`iloc[4]` = same quarter one year ago).

## Git Workflow

Commit work regularly — after each meaningful change (completing a function, fixing a bug, reaching a working state). Use `git push origin main` to sync to GitHub.

## Documentation Reminder

At the end of any session where structural changes were made (new tabs, renamed tabs, new modules, removed features, changed defaults, or schema changes), prompt the user:

> "Docs may be stale — want me to update CLAUDE.md, README.md, summary slidedeck.html, and any relevant PLAN.md files to reflect today's changes?"

Files to keep in sync:
- `CLAUDE.md` — What's Built section, Architecture Decisions, Non-obvious Gotchas
- `README.md` — What This Project Does, Project Structure, What Is Done checklist, Dashboard table
- `summary slidedeck.html` — header description, Visualize step, Build Status list, design pills
- `<module>/PLAN.md` — if the module's design changed

## Non-obvious Gotchas

- **yfinance column naming**: extract.py flattens multi-level column tuples to strings like `"Close AAPL"` (space-separated). transform.py splits on spaces to parse ticker and field. Any yfinance output format change breaks both files.
- **Correlation windows**: "6m" ≈ 126 trading days, "12m" ≈ 252, "24m" ≈ 504, "60m" ≈ 1260. 1m was removed. Sort by date before computing daily returns.
- **No test suite** — use each module's `if __name__ == "__main__"` block to smoke-test during development.
- **Cointegration / Trading Signals / Backtest / Regime Detection / Fundamental Comparison modules live outside `app/`**: `Cointegration test/`, `Trading signals/`, `Backtest/`, `Regime detection agent/`, and `fundamental comparison agent/` are all added to `sys.path` at the top of `streamlit_app.py` — if you move any of them, update the corresponding `sys.path.insert` call.
- **Trading Signals uses `st.session_state`**: results from the Trading Signals tab are stored under `st.session_state["ts_df"]` so the Daily PnL tab can read them without recomputing. If the user navigates to Daily PnL before running signals, they see a prompt to compute first.
- **Quarterly β requires 252-day warmup**: `compute_rolling_signals` only assigns a β after `BETA_WINDOW = 252` trading days of history exist. Rows before that are NaN for beta/spread/z-score. The backtest's 4-year training window ensures this warmup is satisfied well before the test period begins.
- **Decimal types from DB**: `psycopg2` returns `decimal.Decimal` for numeric columns — always cast to `float` before passing to numpy/statsmodels.
- **FRED API 1-day lag**: FRED publishes TIPS yield (`DFII10`) with a 1-business-day delay. The most recent row in `tips_10y` is frequently NaN — this is expected; the dashboard displays "N/A" for that cell.
- **Regime indicator cache TTL = 1 hour**: `_regime_indicators()` in Streamlit is cached for 3600s. First load takes ~30–60s (100+ tickers + FRED). The Refresh button calls `st.cache_data.clear()` to force a reload.
- **NDX-100 component list is hardcoded** in `data_collector.py` — update it when the index rebalances quarterly. Delisted tickers (e.g. WBA, ANSS) cause yfinance warnings but do not break the breadth computation; they are simply excluded from the count.
- **Streamlit does not auto-reload modules outside `app/`** — edits to `Cointegration test/cointegration.py`, `Trading signals/trading_signals.py`, etc. require a Streamlit server restart (`pkill -f "streamlit run"`) to take effect; the file watcher only watches `app/`.
- **Rolling correlation always uses `date.today()` as end date** — `rc_end = date.today()` and `rc_start = rc_end - timedelta(days=1900)` are hardcoded in the Rolling sub-tab, independent of the sidebar date range picker.
- **Fundamental Comparison uses `st.session_state` for loaded data** — results from `fetch_fundamentals()` are stored under `st.session_state["fd_data_a"]` / `"fd_data_b"` after the Load button is clicked. Switching sub-tabs (Income / Balance / Cash Flow / Quality) does not re-fetch. If the user changes the stock pickers without clicking Load, the displayed data is stale — the tab shows a notice.
- **yfinance quarterly statement row names vary by ticker** — `fundamental_data.py` maps yfinance index names (e.g. `"Total Revenue"`) to display names via `INCOME_MAP`, `BALANCE_MAP`, `CASHFLOW_MAP`. If a row is absent for a given ticker (e.g. ARM has no EBITDA), the column is silently NaN — no crash. Check the mapping dicts if a metric unexpectedly shows "—" for all quarters.
- **TSM reports in USD via yfinance ADR** — despite being a TWD-reporting company, the NYSE-listed ADR (TSM) is returned by yfinance in USD. No currency conversion warning is shown for TSM. A banner fires only when `ticker.info["currency"]` returns a non-USD value.
- **Rolling EG p-value uses plain adfuller, not MacKinnon 2010** — `compute_rolling_eg_pvalue()` runs `adfuller(residuals, autolag='AIC')` (not `coint()`) at each rolling step for speed. This gives slightly lower p-values than the proper EG distribution but is acceptable for trend visualisation. The final PASS/FAIL verdict uses `run_engle_granger()` which uses the same plain ADF — both are consistent on this point.
- **Break periods filter ≥ 30 days** — `identify_break_periods()` discards contiguous stretches of rolling p > 0.05 that last fewer than 30 calendar days. These are noise from the rolling window rather than genuine structural events. The 30-day threshold is hardcoded in the function call.
- **Google News RSS returns recent articles** — `_search_google_news()` in `break_commentary.py` fetches from Google News RSS which typically returns articles from the past few weeks. For historical break periods (e.g. 2022–2024), the RSS may return retrospective or analytical articles about those events rather than contemporaneous ones. Claude's citation rules require it to cite only what was returned; if the search is sparse, the commentary will say so rather than fabricate sources.
- **Hardcoded tickers in ETL**: `TICKERS` list is duplicated in both `etl/extract.py` and `etl/transform.py` — keep them in sync. Current list: NVDA, GOOGL, AVGO, ARM, TSM, JPM, BAC.
