# Plan: Implement etl/load.py

## Context

`etl/load.py` is the final ETL stage and the main entry point (`python3 etl/load.py`). It orchestrates extract → transform → load, inserts all data into PostgreSQL, and writes an `etl_log` row on completion. `compute_correlations()` in `transform.py` must also be written since `load.py` depends on it.

---

## Files to Modify / Create

- **`etl/transform.py`** — add `compute_correlations(long_df)` function
- **`etl/load.py`** — create from scratch

---

## Step 1: Add `compute_correlations()` to `etl/transform.py`

**Input:** long-format DataFrame from `reshape()` with columns `date, open, high, low, close, adj_close, volume, symbol`

**Logic:**
1. Sort by `date`
2. Compute daily returns: `adj_close.pct_change()` per ticker group (`.groupby('symbol')`)
3. Pivot to wide format: index=`date`, columns=`symbol`, values=`daily_return`
4. For each period `{'1m': 21, '6m': 126}`:
   - Slice last N rows of the pivot table
   - Call `.corr()` (Pearson, the default)
   - Stack the correlation matrix → rows of `(symbol_1, symbol_2, corr_value)`
   - Keep only unique pairs where `symbol_1 < symbol_2` (avoids duplicates; correlation is symmetric)
   - Add `period` column
5. Concatenate both period DataFrames

**Returns:** DataFrame with columns `symbol_1, symbol_2, period, corr_value`

---

## Step 2: Implement `etl/load.py`

### DB connection

Use `psycopg2` + `python-dotenv`. Load `.env` vars: `DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD`.

### Insertion order (FK dependency chain)

1. `companies` → 2. `company_details` → 3. `stock_prices` → 4. `correlations` → 5. `etl_log`

### Insert functions (all use `ON CONFLICT DO NOTHING`)

**`insert_companies(cur, ticker_info) -> dict[str, int]`**
- `INSERT INTO companies (symbol) VALUES (%s) ON CONFLICT DO NOTHING`
- After inserts, `SELECT id, symbol FROM companies WHERE symbol = ANY(%s)` to get the id mapping
- Returns `{symbol: company_id}` dict used by all subsequent inserts

**`insert_company_details(cur, ticker_info, company_ids)`**
- `INSERT INTO company_details (id, company_name, sector, industry, market_cap) VALUES ... ON CONFLICT DO NOTHING`
- Maps symbol → company_id from the dict

**`insert_stock_prices(cur, long_df, company_ids) -> int`**
- Iterates rows (or uses `executemany`) to insert into `stock_prices`
- `ON CONFLICT (company_id, date) DO NOTHING`
- Returns count of rows inserted (`cur.rowcount` sum)

**`insert_correlations(cur, corr_df, company_ids) -> int`**
- Maps `symbol_1`/`symbol_2` → `company_id_1`/`company_id_2`
- `ON CONFLICT (company_id_1, company_id_2, period) DO NOTHING`
- Returns count of rows inserted

**`log_run(cur, status, rows_inserted, rows_skipped, tickers, duration_sec, error_msg=None)`**
- `INSERT INTO etl_log (status, rows_inserted, rows_skipped, tickers, duration_sec, error_msg) VALUES (...)`

### `run()` orchestrator

```
start = time.time()
try:
    raw_df      = fetch_raw()
    ticker_info = fetch_ticker_info()
    long_df     = reshape(raw_df)
    corr_df     = compute_correlations(long_df)

    conn = get_connection()
    cur  = conn.cursor()

    company_ids = insert_companies(cur, ticker_info)
    insert_company_details(cur, ticker_info, company_ids)
    sp_count   = insert_stock_prices(cur, long_df, company_ids)
    corr_count = insert_correlations(cur, corr_df, company_ids)
    conn.commit()

    log_run(cur, 'success', sp_count + corr_count, ..., tickers, duration)
    conn.commit()

except Exception as e:
    conn.rollback()
    log_run(cur, 'error', 0, 0, tickers, duration, str(e))
    conn.commit()
    raise

finally:
    conn.close()
```

### Imports

`load.py` runs as `python3 etl/load.py` from the project root, so Python adds `etl/` to `sys.path` automatically. Use bare imports: `from extract import fetch_raw, fetch_ticker_info` and `from transform import reshape, compute_correlations`.

---

## Verification

1. Run `python etl/extract.py` — smoke-test that yfinance data fetches cleanly
2. Run `python etl/transform.py` — smoke-test reshape + correlations output shape
3. Run `python3 etl/load.py` — full pipeline run
4. Confirm in psql:
   - `SELECT count(*) FROM companies;` → 5
   - `SELECT count(*) FROM stock_prices;` → ~1260
   - `SELECT count(*) FROM correlations;` → 20 (10 pairs × 2 periods)
   - `SELECT * FROM etl_log ORDER BY run_at DESC LIMIT 1;` → status='success'
5. Re-run `python3 etl/load.py` — row counts should stay the same (idempotent)
