# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Stock correlation analysis pipeline: fetches tech stock prices via yfinance, computes pairwise Pearson correlations, stores results in PostgreSQL, and visualizes via Streamlit.

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
```

## Commands

| Task | Command |
|------|---------|
| Apply schema | `psql -d postgres -f db/schema.sql` |
| Run full ETL | `python3 etl/load.py` |
| Launch dashboard | `streamlit run app/streamlit_app.py` |
| Smoke-test extract | `python etl/extract.py` |
| Smoke-test transform | `python etl/transform.py` |

## What's Built vs. TODO

**Complete:**
- `etl/extract.py` — fetches 1 year OHLCV for AAPL, GOOGL, AVGO, ARM, TSM via yfinance
- `etl/transform.py` — reshapes wide → long format (correlation computation still needed)

**Not yet implemented:**
- `etl/load.py` — insert companies, company_details, stock_prices; write etl_log entries
- `etl/logger.py` — ETL run metadata (status, row_counts, duration, errors)
- `compute_correlations()` in transform.py — 1m and 6m Pearson windows on daily returns
- `app/streamlit_app.py` — correlation heatmap, period selector (1m/6m), ticker filter

## Architecture Decisions

- **Surrogate SERIAL keys** for companies — ticker symbols can change (FB→META); integer PKs are stable
- **Precomputed correlations** stored in `correlations` table — avoids recomputing on every Streamlit render
- **Idempotent ETL** — all inserts use `ON CONFLICT DO NOTHING`; safe to re-run

## Non-obvious Gotchas

- **yfinance column naming**: extract.py flattens multi-level column tuples to strings like `"Close AAPL"` (space-separated). transform.py splits on spaces to parse ticker and field. Any yfinance output format change breaks both files.
- **Hardcoded tickers**: `TICKERS` list is duplicated in both extract.py and transform.py — keep them in sync.
- **Correlation windows**: "1m" ≈ 21 trading days, "6m" ≈ 126 trading days. Sort by date before computing daily returns.
- **No test suite** — use each module's `if __name__ == "__main__"` block to smoke-test during development.
