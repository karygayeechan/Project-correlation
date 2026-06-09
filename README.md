# Stock Correlation Analysis

Analyzes return correlations between tech stocks using a PostgreSQL database, automated ETL pipeline, Streamlit dashboard, AI commentary agent, statistical cointegration tests, and rolling pairs-trading signals.

## What This Project Does

1. **Extracts** daily OHLCV stock data from Yahoo Finance (via `yfinance`) for 5 years across tickers: NVDA, GOOGL, AVGO, ARM, TSM (and any dynamically added tickers)
2. **Transforms** the raw data into normalized rows and computes pairwise return correlations over 1-month and 6-month windows
3. **Loads** everything into PostgreSQL
4. **Visualizes** correlations in a 6-tab Streamlit dashboard with interactive controls and ticker management
5. **Generates AI commentary** — after each ETL run, an agent compares the latest correlations against a 30-day baseline and writes a plain-English regime summary via the Claude API
6. **Cointegration testing** — ADF test on individual price series + bidirectional Engle-Granger test (both A→B and B→A) to determine if a pair shares a stable long-run relationship
7. **Trading signals** — rolling 90-day hedge ratio, z-score spread signals (LONG/SHORT/EXIT), daily position sizing, and PnL tracking
8. **Backtesting** — 4-year training warm-up + 1-year out-of-sample evaluation with performance, risk, stability, and scalability metrics

---

## Database Schema

7 tables covering all required relationship types:

| Table | Relationship | Description |
|---|---|---|
| `companies` | parent | ticker symbol |
| `company_details` | 1:1 with companies | name, sector, industry, market cap |
| `stock_prices` | 1:N with companies | daily OHLCV + adjusted close |
| `correlations` | self-join on companies | latest pairwise Pearson correlation by period (upserted each run) |
| `correlation_history` | self-join on companies | timestamped snapshot of every ETL run's correlation values |
| `correlation_alerts` | standalone | AI-generated plain-English commentary comparing current vs. baseline |
| `etl_log` | standalone | one row per ETL run with status and row counts |

---

## Project Structure

```
project_correlation/
├── db/
│   └── schema.sql                        # CREATE TABLE / CREATE INDEX (IF NOT EXISTS — idempotent)
├── etl/
│   ├── extract.py                        # fetch raw 5-year OHLCV + metadata from yfinance
│   ├── transform.py                      # reshape wide→long; compute 1m/6m Pearson correlations
│   └── load.py                           # insert companies, prices, correlations; archive snapshot; run commentary agent
├── agent/
│   ├── __init__.py
│   ├── commentary.py                     # AI commentary agent — compares correlation snapshots, calls Claude API
│   └── AGENT.md                          # detailed breakdown of the agent implementation
├── api/
│   └── main.py                           # FastAPI backend — REST endpoints over the DB
├── app/
│   ├── db.py                             # read-only DB query layer (used by the API)
│   ├── api_client.py                     # HTTP client wrapping the API (used by Streamlit)
│   └── streamlit_app.py                  # 6-tab Streamlit dashboard
├── Cointegration test/
│   ├── cointegration.py                  # ADF + bidirectional Engle-Granger computation module
│   ├── conclusions.py                    # plain-English verdict strings
│   ├── PLAN.md                           # implementation plan
│   └── Cointegration test instruction    # original spec
├── Trading signals/
│   ├── trading_signals.py                # rolling hedge ratio, z-score signals, PnL
│   ├── PLAN.md                           # implementation plan
│   └── Trading signals (...) instructions  # original spec
├── Backtest/
│   ├── backtest.py                       # 4yr/1yr train-test split; performance, risk, stability, scalability metrics
│   └── PLAN.md                           # implementation plan
└── README.md
```

---

## Running the Application

Requires PostgreSQL running and ETL executed at least once (see Setup below).

**Terminal 1 — API backend:**
```bash
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Streamlit dashboard:**
```bash
source .venv/bin/activate
streamlit run app/streamlit_app.py
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:8501 |
| API (interactive docs) | http://localhost:8000/docs |

---

## What Is Done

- [x] Database schema (`db/schema.sql`) — 7 tables, indexes, constraints, `IF NOT EXISTS` for safe re-runs
- [x] `etl/extract.py` — fetches 5 years of daily prices + company metadata from yfinance
- [x] `etl/transform.py` — reshapes wide DataFrame to long format; `compute_correlations()` produces 1m/6m Pearson pairs
- [x] `etl/load.py` — inserts companies, company_details, stock_prices (`ON CONFLICT DO NOTHING`); upserts correlations (`ON CONFLICT DO UPDATE`); archives snapshot to `correlation_history`; calls commentary agent; writes `etl_log` row on success or error; accepts dynamic ticker list
- [x] ETL logging — every pipeline run writes status, row counts, duration, and any error to `etl_log`
- [x] Schema applied to PostgreSQL — all 7 tables and indexes created
- [x] `app/db.py` — read-only query layer: tickers, stock prices, on-the-fly correlation matrices, rolling correlations, alerts, ETL log
- [x] `api/main.py` — FastAPI backend with 10 REST endpoints (health, tickers CRUD, prices, correlations heatmap/rolling, alerts, ETL log/run); interactive docs at `/docs`
- [x] `app/api_client.py` — httpx client wrapping the API; mirrors `db.py` signatures so Streamlit needs no DB access
- [x] `app/streamlit_app.py` — 6-tab dashboard (see Dashboard section below)
- [x] Tickers finalized — NVDA, GOOGL, AVGO, ARM, TSM (dynamically extensible via Manage Tickers)
- [x] `agent/commentary.py` — AI commentary agent comparing monthly correlation snapshots via Claude API
- [x] `correlation_history` table — accumulates one snapshot per ETL day per pair; enables 30-day delta comparisons
- [x] `correlation_alerts` table — stores generated commentary with current and baseline dates
- [x] `Cointegration test/cointegration.py` — ADF on each series + **bidirectional** Engle-Granger (A→B and B→A); primary direction = lower p-value; both results shown on dashboard
- [x] `Cointegration test/conclusions.py` — plain-English verdict strings
- [x] `Trading signals/trading_signals.py` — rolling 90-day OLS hedge ratio, z-score signals, stateful positions with daily β refresh, daily PnL
- [x] `Backtest/backtest.py` — 4yr train / 1yr test split; performance, trading activity, risk, stability, and scalability metrics; in-memory only (no DB writes)
- [x] Manage Tickers ETL corrected to 5-year data fetch (was labelled 1y in UI)

---

## Dashboard

Launch with `streamlit run app/streamlit_app.py`. Opens at **http://localhost:8501**.

The sidebar provides a global ticker multiselect and date range that feed all chart tabs.

| Tab | Type | Description |
|---|---|---|
| **Correlation** | Read | Three sub-tabs: **Heatmap** (pairwise Pearson r matrix; toggle tickers, period, end date; ranked pairs table), **Rolling** (pair correlation over time with selectable window: 21/42/63/126 days), **Network Graph** (circular graph; edge thickness/color encode correlation strength; threshold slider). |
| **Cointegration** | Read | ADF test on each price series to confirm non-stationarity, then **bidirectional** Engle-Granger test (A→B and B→A). Primary direction = lower spread ADF p-value; both results shown with ★ badge. Pass/fail verdict with plain-English conclusions. Default pair: MSFT/META. |
| **Trading Signals** | Read | Rolling 90-day pairs strategy for any two tickers. Computes hedge ratio β, spread z-score, and generates LONG/SHORT/EXIT signals. Shows current trade instruction, z-score chart, rolling β, and signal log. Hypothetical 5-year PnL section at bottom (cumulative PnL, Sharpe, max drawdown, win rate). Default pair: MSFT/META. |
| **Backtest (4yr/1yr)** | Read | Out-of-sample evaluation: 4-year rolling warm-up + last 1 year as test slice. Five sections: Performance (Sharpe, Calmar, drawdown, win rate), Trading Activity (trade count, holding period, cost sensitivity), Risk (vol, VaR, CVaR, skew, kurtosis), Stability (rolling ADF, z-score histogram, β series, rolling half-life), Scalability (1×/2×/5× position size comparison). Default pair: MSFT/META. |
| **Regime Alerts** | Read | Latest AI-generated commentary comparing correlations against a 30-day baseline. Only populated once ≥ 30 days of history exists. Full alert history table below the latest summary. |
| **Manage Tickers** | Write | Add a ticker (fetches 5 years, triggers ETL for all current + new), remove a ticker (cascades deletes from all ticker-linked tables), or refresh all data. |

---

## Development Environment Setup

### Requirements

- Python 3.11.9
- PostgreSQL 16
- `uv` package manager (recommended) or `pip`
- Anthropic API key (for the commentary agent — optional, ETL works without it)

### 1. Install PostgreSQL (macOS)

```bash
brew install postgresql@16
brew services start postgresql@16
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### 2. Clone and enter the project

```bash
cd project_correlation
```

### 3. Create a virtual environment

Using `uv` (recommended):
```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv fastapi uvicorn httpx anthropic statsmodels
```

Using standard `pip`:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv fastapi uvicorn httpx anthropic statsmodels
```

### 4. Configure environment variables

Create a `.env` file at the project root:
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=<your_pg_user>
DB_PASSWORD=<your_pg_password>
ANTHROPIC_API_KEY=<your_api_key>
```

`ANTHROPIC_API_KEY` is optional — the ETL and dashboard work without it. Regime alerts will simply be skipped until it is set.

### 5. Apply the database schema

```bash
psql -d postgres -f db/schema.sql
```

### 6. Run the ETL

```bash
python3 etl/load.py
```

### 7. Start the API backend

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive docs available at **http://localhost:8000/docs** once running.

### 8. Launch the dashboard

```bash
streamlit run app/streamlit_app.py
```

The dashboard calls the API at `http://localhost:8000` by default.
Override with `API_URL=http://your-host:8000` if running on a different host.

---

## Key Design Decisions

- **Surrogate keys** (`SERIAL`) — ticker symbols can change (e.g. FB → META); integer PKs are stable
- **Correlations upserted, not inserted** — `ON CONFLICT DO UPDATE SET corr_value = ...` ensures every ETL run refreshes correlation values to the latest window
- **Stock prices idempotent** — `ON CONFLICT DO NOTHING` on `(company_id, date)`; historical prices don't change
- **Correlations computed on-the-fly for the dashboard** — heatmap and rolling correlation tabs query `stock_prices` directly rather than the precomputed `correlations` table, enabling arbitrary end-date and window selection
- **`adj_close` stored separately from `close`** — adjusted price accounts for splits/dividends and is used for return calculations; raw close reflects the actual traded price
- **Dynamic ticker support** — `run(tickers=[...])` in `load.py` accepts any ticker list; the dashboard's Manage Tickers tab can add/remove tickers without touching code
- **Three-tier architecture** — Streamlit (port 8501) → FastAPI (port 8000) → PostgreSQL (port 5432); `app/db.py` is used only by the API, never directly by the dashboard
- **30-day minimum for commentary** — sub-month correlation comparisons are too noisy to interpret; the agent skips silently until a baseline snapshot ≥ 30 calendar days old exists in `correlation_history`
- **Commentary failures are non-fatal** — if the Claude API call fails (network error, rate limit, missing key), the ETL rolls back only the alert INSERT and continues to completion; no price or correlation data is lost
- **One snapshot per day** — `correlation_history` uses a `UNIQUE (company_id_1, company_id_2, period, snapshot_date)` constraint so re-running the ETL multiple times on the same day is idempotent
