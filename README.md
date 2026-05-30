# Stock Correlation Analysis

Analyzes return correlations between tech stocks using a PostgreSQL database, automated ETL pipeline, and Streamlit dashboard.

## What This Project Does

1. **Extracts** daily OHLCV stock data from Yahoo Finance (via `yfinance`) for 5 tickers: AAPL, GOOGL, AVGO, ARM, TSM
2. **Transforms** the raw data into normalized rows and computes pairwise return correlations over 1-month and 6-month windows
3. **Loads** everything into PostgreSQL
4. **Visualizes** correlations in a Streamlit dashboard where users can adjust tickers, time periods, and date ranges

---

## Database Schema

4 tables covering all required relationship types:

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
│   └── schema.sql          # all CREATE TABLE and CREATE INDEX statements
├── etl/
│   ├── extract.py          # fetch raw data from yfinance
│   ├── transform.py        # reshape wide->long, compute correlations
│   ├── load.py             # insert into PostgreSQL (TODO)
│   └── logger.py           # ETL run logging (TODO)
├── app/
│   └── streamlit_app.py    # Streamlit dashboard (TODO)
└── README.md
```

---

## What Is Done

- [x] Database schema (`db/schema.sql`) with keys, indexes, constraints, normalization
- [x] `etl/extract.py` - fetches 1 year of daily prices + company metadata from yfinance
- [x] `etl/transform.py` - reshapes wide DataFrame to long format (one row per ticker per date)

## What Still Needs to Be Done

- [ ] `etl/load.py` - insert companies, company_details, stock_prices into Postgres; idempotent (ON CONFLICT DO NOTHING)
- [ ] `etl/transform.py` - add `compute_correlations()` function (daily returns → pairwise Pearson correlation for `1m` and `6m` periods)
- [ ] `etl/logger.py` - write ETL run metadata to `etl_log` table
- [ ] Apply schema to Postgres (`psql -d postgres -f db/schema.sql`)
- [ ] `app/streamlit_app.py` - dashboard with correlation heatmap/chart, period selector, ticker filter

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
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv
```

Using standard `pip`:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv
```

### 4. Apply the database schema

```bash
psql -d postgres -f db/schema.sql
```

### 5. Run the ETL

```bash
python3 etl/load.py
```

### 6. Launch the dashboard

```bash
streamlit run app/streamlit_app.py
```

---

## Key Design Decisions

- **Surrogate keys** (`SERIAL`) used throughout -- ticker symbols can change (e.g. FB -> META), integer IDs never do
- **Correlations are precomputed** and stored in the DB -- avoids recomputing on every Streamlit interaction
- **ETL is idempotent** -- re-running will not duplicate data (`ON CONFLICT DO NOTHING`)
- **`adj_close` stored separately from `close`** -- adjusted price accounts for splits/dividends and is used for return calculations; raw close reflects the actual traded price
