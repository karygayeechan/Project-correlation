# Stock Correlation Analysis

Analyzes return correlations between tech stocks and monitors macro market regimes using a PostgreSQL database, automated ETL pipeline, Streamlit dashboard, macro regime-detection agent, AI-written commentary, statistical cointegration tests, and rolling pairs-trading signals.

## What This Project Does

1. **Extracts** daily OHLCV stock data from Yahoo Finance (via `yfinance`) for 5 years across tickers: NVDA, GOOGL, AVGO, ARM, TSM (and any dynamically added tickers)
2. **Transforms** the raw data into normalized rows and computes pairwise return correlations over **6-month, 12-month, and 24-month** windows
3. **Loads** everything into PostgreSQL
4. **Visualizes** correlations in a 6-tab Streamlit dashboard with interactive controls and ticker management
5. **Monitors macro regime** — live indicator engine tracks 10Y Treasury yield, TIPS real yield, Nasdaq-100 breadth (% above 200DMA), VIX trend, and SMH/QQQ relative strength across 10 alert rules with severity levels and recent-crossing detection
6. **Cointegration testing** — ADF prerequisite check (5-year data) + bidirectional Engle-Granger spread charts on **5-year** and **2-year** data + quarterly p-values on **4 quarterly windows within the past 1 year**, all showing both directions (A regressed on B and B regressed on A)
7. **Trading signals** — **quarterly-fixed** hedge ratio β (trailing 1-year OLS, refreshed at each calendar-quarter boundary), z-score spread signals (LONG/SHORT/EXIT), position sizing, and PnL tracking
8. **Backtesting** — 4-year training warm-up + 1-year out-of-sample evaluation with performance, risk, stability, and scalability metrics
9. **AI regime commentary** — on-demand ~100-word Claude briefing summarising live macro conditions and triggered alerts; one entry cached per calendar day in the database

---

## Database Schema

7 tables covering all required relationship types:

| Table | Relationship | Description |
|---|---|---|
| `companies` | parent | ticker symbol |
| `company_details` | 1:1 with companies | name, sector, industry, market cap |
| `stock_prices` | 1:N with companies | daily OHLCV + adjusted close |
| `correlations` | self-join on companies | latest pairwise Pearson correlation by period (upserted each run) |
| `correlation_history` | self-join on companies | timestamped correlation snapshots — kept in schema, no longer written to by ETL |
| `correlation_alerts` | standalone | AI-generated ~100-word macro regime briefings (one per calendar day) |
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
│   └── load.py                           # insert companies, prices, correlations; write etl_log
├── Regime detection agent/
│   ├── data_collector.py                 # fetch 5 macro indicators via yfinance + FRED
│   ├── regime_alerts.py                  # 10 alert rules across 5 indicator families
│   ├── commentary.py                     # on-demand AI regime briefing via Claude API
│   ├── PLAN.md                           # indicator sources, alert rules, thresholds, architecture
│   └── AGENT.md                          # legacy agent documentation
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
│   ├── trading_signals.py                # quarterly-fixed β, z-score signals, PnL
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
- [x] `etl/transform.py` — reshapes wide DataFrame to long format; `compute_correlations()` produces **6m/12m/24m** Pearson pairs
- [x] `etl/load.py` — inserts companies, company_details, stock_prices (`ON CONFLICT DO NOTHING`); upserts correlations (`ON CONFLICT DO UPDATE`); writes `etl_log` row on success or error; accepts dynamic ticker list
- [x] ETL logging — every pipeline run writes status, row counts, duration, and any error to `etl_log`
- [x] Schema applied to PostgreSQL — all 7 tables and indexes created
- [x] `app/db.py` — read-only query layer: tickers, stock prices, on-the-fly correlation matrices, rolling correlations, alerts, ETL log
- [x] `api/main.py` — FastAPI backend with 10 REST endpoints (health, tickers CRUD, prices, correlations heatmap/rolling, alerts, ETL log/run); interactive docs at `/docs`
- [x] `app/api_client.py` — httpx client wrapping the API; mirrors `db.py` signatures so Streamlit needs no DB access
- [x] `app/streamlit_app.py` — 6-tab dashboard (see Dashboard section below)
- [x] Tickers finalized — NVDA, GOOGL, AVGO, ARM, TSM (dynamically extensible via Manage Tickers)
- [x] `Regime detection agent/data_collector.py` — fetches 10Y yield (yfinance `^TNX`), TIPS real yield (FRED `DFII10`), Nasdaq-100 breadth (computed across ~98 NDX components), VIX (`^VIX`), SMH/QQQ ratio + z-score
- [x] `Regime detection agent/regime_alerts.py` — 10 alert rules: yield crossovers (50DMA, 200DMA, 20d ROC), TIPS level + trend + monthly rise, NDX breadth zones, VIX 20/100DMA, SMH/QQQ 100DMA + death cross
- [x] `Regime detection agent/commentary.py` — on-demand ~100-word AI regime briefing via Claude; cached one per calendar day in `correlation_alerts`
- [x] `Cointegration test/cointegration.py` — three data windows: **5-year** (ADF banner + bidirectional EG spread charts), **2-year** (additional bidirectional EG spread charts), **1-year** split into **4 quarterly windows** for EG p-values; all sections show both directions (A→B and B→A) with explicit regression labels and ★ primary marker
- [x] `Cointegration test/conclusions.py` — plain-English verdict strings
- [x] `Trading signals/trading_signals.py` — **quarterly-fixed** β estimated from trailing 1-year OLS at each calendar-quarter boundary; z-score window 60–120 days (default 90); stateful positions with quarterly β, daily PnL
- [x] `Backtest/backtest.py` — 4yr train / 1yr test split; quarterly-fixed β (inherited from trading_signals); performance, trading activity, risk, stability, and scalability metrics; in-memory only (no DB writes)
- [x] Manage Tickers ETL corrected to 5-year data fetch (was labelled 1y in UI)

---

## Dashboard

Launch with `streamlit run app/streamlit_app.py`. Opens at **http://localhost:8501**.

The sidebar provides a global ticker multiselect and date range that feed all chart tabs.

| Tab | Type | Description |
|---|---|---|
| **Correlation** | Read | Three sub-tabs: **Heatmap** (pairwise Pearson r; period radio **6m / 12m / 24m**, default 24m; ranked pairs table shows all three period columns sorted by selected period), **Rolling** (90-day rolling window over a fixed **5-year span** ending today; always current regardless of sidebar), **Network Graph** (circular graph; period radio **24m / 60m**, default 24m; default |r| threshold 0.65). |
| **Cointegration** | Read | ADF banner (5-year data; alerts if any series is stationary). Then **four EG spread charts**: **5-year primary** and **5-year reverse** direction, followed by **2-year primary** and **2-year reverse** direction — each explicitly titled with its data period and regression direction. Below that, **quarterly p-values (1-year)** split into 4 equal windows (~63 trading days each), each card showing both directions with "X regressed on Y" labels and ★ on the primary (lower p-value). Overall verdict: all 4 quarters must pass. Default pair: ARM/TSM. |
| **Trading Signals** | Read | Quarterly-fixed β pairs strategy for any two tickers. β estimated from trailing 1-year OLS at each calendar-quarter boundary, held fixed for the quarter — visualised as a step-function chart. Z-score window: 60–120 days (default 90). Shows current trade instruction, z-score chart, quarterly β chart, and signal log (with active quarter column). Hypothetical 5-year PnL at bottom. Default pair: ARM/TSM. |
| **Backtest (4yr/1yr)** | Read | Out-of-sample evaluation: 4-year warm-up (calibrates quarterly β) + last 1 year as test slice. Five sections: Performance (Sharpe, Calmar, drawdown, win rate), Trading Activity (trade count, holding period, cost sensitivity), Risk (vol, VaR, CVaR, skew, kurtosis), Stability (rolling ADF, z-score histogram, quarterly β step-function + update count, rolling half-life), Scalability (1×/2×/5× position size comparison). Default pair: ARM/TSM. |
| **Regime Alerts** | Read | Two sections: **Macro Regime Indicators** — live 5-indicator dashboard with 10 color-coded alert rules (🔴 critical / 🟡 warning / 🟢 OK), expandable per-indicator blocks, and triggered-alerts summary table; **AI Regime Commentary** — on-demand ~100-word Claude briefing on rate environment, breadth, volatility, and sector momentum; last 5 days of history shown. |
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
uv pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv fastapi uvicorn httpx anthropic statsmodels fredapi
```

Using standard `pip`:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install yfinance pandas psycopg2-binary streamlit plotly python-dotenv fastapi uvicorn httpx anthropic statsmodels fredapi
```

### 4. Configure environment variables

Create a `.env` file at the project root:
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=<your_pg_user>
DB_PASSWORD=<your_pg_password>
ANTHROPIC_API_KEY=<your_api_key>   # needed for AI Regime Commentary button
FRED_API_KEY=<free_key>            # free from fred.stlouisfed.org — needed for TIPS real yield
```

`ANTHROPIC_API_KEY` is optional — ETL and dashboard work without it; the Generate Commentary button will show an error if unset. `FRED_API_KEY` is required for the TIPS indicator; without it, `tips_10y` will be unavailable in the Regime Alerts tab.

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
- **Regime commentary is on-demand** — fetching ~100 NDX tickers for breadth takes 30–60s; running this on every ETL would be disruptive. Commentary is generated when the user clicks the button; one entry per calendar day is cached in `correlation_alerts`
- **QQQ as trading-calendar anchor** — `^TNX` and `^VIX` (CBOE) trade on some days equities don't. The joint yfinance download is filtered to rows where QQQ is non-NaN, preventing NaN contamination of rolling window calculations
- **`correlation_history` retained but dormant** — table exists in schema for backwards compatibility; ETL no longer writes to it
