# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Stock correlation analysis pipeline: fetches tech stock prices via yfinance, computes pairwise Pearson correlations, stores results in PostgreSQL, visualizes via Streamlit, and generates AI-written regime commentary via the Claude API.

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
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv
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
- `etl/extract.py` — fetches 1 year OHLCV for NVDA, GOOGL, AVGO, ARM, TSM via yfinance
- `etl/transform.py` — reshapes wide → long; `compute_correlations()` produces 1m/6m Pearson pairs
- `etl/load.py` — inserts companies, prices, upserts correlations, archives snapshot, runs commentary agent, writes etl_log

**Agent:**
- `agent/commentary.py` — compares latest correlations against 30-day baseline, calls Claude API, stores result in `correlation_alerts`

**API:**
- `api/main.py` — FastAPI backend with 10 endpoints; `app/db.py` is the query layer

**Dashboard:**
- `app/streamlit_app.py` — 9-tab Streamlit dashboard (heatmap, rolling corr, prices, scatter, network, volatility, regime alerts, manage tickers, ETL log)
- `app/api_client.py` — HTTP client so Streamlit never touches the DB directly

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

## Non-obvious Gotchas

- **yfinance column naming**: extract.py flattens multi-level column tuples to strings like `"Close AAPL"` (space-separated). transform.py splits on spaces to parse ticker and field. Any yfinance output format change breaks both files.
- **Hardcoded tickers**: `TICKERS` list is duplicated in both extract.py and transform.py — keep them in sync.
- **Correlation windows**: "1m" ≈ 21 trading days, "6m" ≈ 126 trading days. Sort by date before computing daily returns.
- **No test suite** — use each module's `if __name__ == "__main__"` block to smoke-test during development.
