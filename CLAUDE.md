# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Stock correlation analysis pipeline: fetches tech stock prices via yfinance, computes pairwise Pearson correlations, stores results in PostgreSQL, visualizes via Streamlit, generates AI-written regime commentary via the Claude API, and runs statistical cointegration tests and rolling pairs-trading signals.

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
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv statsmodels
```

### Required env vars
Create a `.env` at the project root (loaded via `python-dotenv`):
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=<your_pg_user>
DB_PASSWORD=<your_pg_password>
ANTHROPIC_API_KEY=<your_api_key>   # optional — ETL works without it; needed for Regime Alerts
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
- `etl/extract.py` — fetches 5 years OHLCV for NVDA, GOOGL, AVGO, ARM, TSM via yfinance
- `etl/transform.py` — reshapes wide → long; `compute_correlations()` produces 1m/6m Pearson pairs
- `etl/load.py` — inserts companies, prices, upserts correlations, archives snapshot, runs commentary agent, writes etl_log

**Agent:**
- `agent/commentary.py` — compares latest correlations against 30-day baseline, calls Claude API, stores result in `correlation_alerts`

**API:**
- `api/main.py` — FastAPI backend with 10 endpoints; `app/db.py` is the query layer

**Dashboard:**
- `app/streamlit_app.py` — 6-tab Streamlit dashboard: Correlation (sub-tabs: Heatmap / Rolling / Network Graph), Cointegration, Trading Signals (+ hypothetical 5-year PnL), Backtest (4yr/1yr), Regime Alerts, Manage Tickers
- `app/api_client.py` — HTTP client so Streamlit never touches the DB directly

**Cointegration (`Cointegration test/`):**
- `cointegration.py` — ADF test on each price series; Engle-Granger run in **both directions** (A→B and B→A); primary direction = lower p-value; both results shown on dashboard
- `conclusions.py` — plain-English verdict strings for each test result

**Trading Signals (`Trading signals/`):**
- `trading_signals.py` — rolling 90-day OLS hedge ratio, spread, z-score, LONG/SHORT/EXIT/HOLD signals, daily position sizing (`position_B = −β_t × position_A`), daily PnL
- Hypothetical 5-year PnL (cumulative, daily bars, monthly breakdown) is rendered at the bottom of the Trading Signals tab

**Backtest (`Backtest/`):**
- `backtest.py` — 4y/1y train-test split (4 years warm-up, last 1 year evaluated); in-memory only; computes performance, trading activity, risk, stability, and scalability metrics
- `PLAN.md` — implementation notes and design decisions
- `Backtest instruction` — original specification for the backtest tab

**Database:**
- 7 tables: `companies`, `company_details`, `stock_prices`, `correlations`, `correlation_history`, `correlation_alerts`, `etl_log`

## Architecture Decisions

- **Surrogate SERIAL keys** for companies — ticker symbols can change (FB→META); integer PKs are stable
- **Precomputed correlations** stored in `correlations` table — avoids recomputing on every Streamlit render
- **Idempotent ETL** — all inserts use `ON CONFLICT DO NOTHING`; safe to re-run
- **`correlation_history` snapshots** — one row per pair/period/day; enables 30-day delta comparisons for the commentary agent without touching the live `correlations` table
- **30-day commentary minimum** — sub-month correlation deltas are too noisy; the agent skips silently until a baseline snapshot ≥ 30 calendar days old exists
- **Commentary is non-fatal** — Claude API failure rolls back only the alert INSERT; prices and correlations are already committed and unaffected

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
- **Hardcoded tickers**: `TICKERS` list is duplicated in both extract.py and transform.py — keep them in sync.
- **Correlation windows**: "1m" ≈ 21 trading days, "6m" ≈ 126 trading days. Sort by date before computing daily returns.
- **No test suite** — use each module's `if __name__ == "__main__"` block to smoke-test during development.
- **Cointegration / Trading Signals / Backtest modules live outside `app/`**: `Cointegration test/`, `Trading signals/`, and `Backtest/` are added to `sys.path` at the top of `streamlit_app.py` — if you move them, update those `sys.path.insert` calls.
- **Trading Signals uses `st.session_state`**: results from the Trading Signals tab are stored under `st.session_state["ts_df"]` so the Daily PnL tab can read them without recomputing. If the user navigates to Daily PnL before running signals, they see a prompt to compute first.
- **Decimal types from DB**: `psycopg2` returns `decimal.Decimal` for numeric columns — always cast to `float` before passing to numpy/statsmodels.
