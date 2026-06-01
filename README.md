# Stock Correlation Analysis

Analyzes return correlations between tech stocks using a PostgreSQL database, automated ETL pipeline, and Streamlit dashboard.

## What This Project Does

1. **Extracts** daily OHLCV stock data from Yahoo Finance (via `yfinance`) for 5 tickers: NVDA, GOOGL, AVGO, ARM, TSM
2. **Transforms** the raw data into normalized rows and computes pairwise return correlations over 1-month and 6-month windows
3. **Loads** everything into PostgreSQL
4. **Visualizes** correlations in an 8-tab Streamlit dashboard with interactive controls, ticker management, and ETL logging

---

## Database Schema

5 tables covering all required relationship types:

| Table | Relationship | Description |
|---|---|---|
| `companies` | parent | ticker symbol |
| `company_details` | 1:1 with companies | name, sector, industry, market cap |
| `stock_prices` | 1:N with companies | daily OHLCV + adjusted close |
| `correlations` | self-join on companies | pairwise Pearson correlation by period |
| `etl_log` | standalone | one row per ETL run with status and row counts |

---

## Project Structure

```
project_correlation/
├── db/
│   └── schema.sql              # CREATE TABLE / CREATE INDEX (IF NOT EXISTS — idempotent)
├── etl/
│   ├── extract.py              # fetch raw OHLCV + metadata from yfinance
│   ├── transform.py            # reshape wide→long; compute 1m/6m Pearson correlations
│   └── load.py                 # insert companies, prices, correlations; write etl_log row
├── api/
│   └── main.py                 # FastAPI backend — REST endpoints over the DB
├── app/
│   ├── db.py                   # read-only DB query layer (used by the API)
│   ├── api_client.py           # HTTP client wrapping the API (used by Streamlit)
│   └── streamlit_app.py        # 8-tab Streamlit dashboard
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

- [x] Database schema (`db/schema.sql`) — 5 tables, indexes, constraints, `IF NOT EXISTS` for safe re-runs
- [x] `etl/extract.py` — fetches 1 year of daily prices + company metadata from yfinance
- [x] `etl/transform.py` — reshapes wide DataFrame to long format; `compute_correlations()` produces 1m/6m Pearson pairs
- [x] `etl/load.py` — inserts companies, company_details, stock_prices (`ON CONFLICT DO NOTHING`); upserts correlations (`ON CONFLICT DO UPDATE`); writes `etl_log` row on success or error; accepts dynamic ticker list
- [x] ETL logging — every pipeline run writes status, row counts, duration, and any error to `etl_log`
- [x] Schema applied to PostgreSQL — all 5 tables and indexes created
- [x] `app/db.py` — read-only query layer: tickers, stock prices, on-the-fly correlation matrices, rolling correlations, ETL log
- [x] `api/main.py` — FastAPI backend with 9 REST endpoints (health, tickers CRUD, prices, correlations heatmap/rolling, ETL log/run); interactive docs at `/docs`
- [x] `app/api_client.py` — httpx client wrapping the API; mirrors `db.py` signatures so Streamlit needs no DB access
- [x] `app/streamlit_app.py` — full 8-tab dashboard (see Dashboard section below)
- [x] Tickers finalized — NVDA, GOOGL, AVGO, ARM, TSM (AAPL replaced with NVDA)

---

## Dashboard

Launch with `streamlit run app/streamlit_app.py`. Opens at **http://localhost:8501**.

The sidebar provides a global ticker multiselect and date range that feed all chart tabs.

| Tab | Type | Description |
|---|---|---|
| **Correlation Heatmap** | Read | Pairwise Pearson r matrix computed from DB prices. Toggle tickers, period (1m/6m), and end date to explore historical correlation regimes. Ranked pairs table below the heatmap. |
| **Rolling Correlation** | Read | Picks a ticker pair and rolling window (21/42/63/126 days). Plots how the correlation evolves over time — useful for spotting regime changes or event-driven decoupling. |
| **Price & Returns** | Read | Normalized price (base = 100 at window start) and daily returns bar chart. Toggle between views or show both. |
| **Pair Scatter** | Read | Daily return scatter for any two tickers with OLS trendline. Shows Pearson r, R², and beta. |
| **Network Graph** | Read | Circular graph where edge thickness and color encode correlation strength (green = positive, red = negative). Threshold slider removes weak edges. |
| **Volatility Tracker** | Read | Rolling annualized realized volatility (σ × √252) per ticker. Contextualizes correlations — high vol changes how co-movement translates to portfolio risk. |
| **Manage Tickers** | Write | Add a ticker (triggers ETL for all current + new), remove a ticker (cascades deletes), or refresh all data. All operations update the DB and reload charts immediately. |
| **ETL Log** | Read | Table of all pipeline runs — timestamp, status, tickers processed, rows inserted/skipped, duration, and error message if failed. Auto-refreshes after any write. |

---

## Development Environment Setup

### Requirements

- Python 3.11.9
- PostgreSQL 16
- `uv` package manager (recommended) or `pip`

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
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv fastapi uvicorn httpx
```

Using standard `pip`:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv fastapi uvicorn httpx
```

### 4. Configure environment variables

Create a `.env` file at the project root:
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=<your_pg_user>
DB_PASSWORD=<your_pg_password>
```

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
