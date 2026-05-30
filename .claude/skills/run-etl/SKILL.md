---
name: run-etl
description: Run the full ETL pipeline (extract → transform → load) in the correct sequence. Use when the user wants to refresh stock data, run the pipeline, or test a specific ETL step.
disable-model-invocation: false
---

Run the ETL pipeline in order:

1. **Extract** (`python etl/extract.py`) — fetches 1 year of daily OHLCV data for AAPL, GOOGL, AVGO, ARM, TSM from yfinance
2. **Transform** (`python etl/transform.py`) — reshapes wide → long format and computes 1m/6m Pearson correlations
3. **Load** (`python etl/load.py`) — inserts companies, stock_prices, correlations into PostgreSQL using ON CONFLICT DO NOTHING; writes an etl_log row on completion

Before running, verify:
- PostgreSQL 16 is running: `brew services list | grep postgresql`
- Schema tables exist: `psql -d postgres -c "\dt"` should list companies, company_details, stock_prices, correlations, etl_log
- `.env` file is present with DB connection details

If any step fails, report the exact error and which step failed. After a successful load, confirm by querying `SELECT * FROM etl_log ORDER BY started_at DESC LIMIT 1`.
