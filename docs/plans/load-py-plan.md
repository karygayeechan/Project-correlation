# Reference: etl/load.py Implementation

> **Status: Implemented.** This document describes the actual working implementation, updated from the original pre-build plan.

## Overview

`etl/load.py` is the main ETL entry point (`python3 etl/load.py`). It orchestrates extract → transform → load → archive → AI commentary. All inserts are idempotent via `ON CONFLICT DO NOTHING`.

---

## Files Modified

- **`etl/transform.py`** — `compute_correlations(long_df)` added
- **`etl/load.py`** — full implementation
- **`agent/commentary.py`** — AI commentary agent (called from load.py)
- **`etl/backfill_history.py`** — one-time script to backfill `correlation_history` from existing `stock_prices`

---

## sys.path Setup

`load.py` needs to import from both `etl/` (siblings: `extract`, `transform`) and the project root (`agent.commentary`). It explicitly inserts both:

```python
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                     # etl/ — for extract, transform
sys.path.insert(0, os.path.join(_HERE, "..")) # project root — for agent/
```

---

## Step 1: `compute_correlations()` in `etl/transform.py`

**Input:** long-format DataFrame from `reshape()` — columns `date, open, high, low, close, adj_close, volume, symbol`

**Logic:**
1. Sort by `date`
2. Compute daily returns: `adj_close.pct_change()` per ticker via `.groupby('symbol')`
3. Pivot to wide: index=`date`, columns=`symbol`, values=`daily_return`
4. For each period `{'1m': 21, '6m': 126}`:
   - Slice last N rows
   - Call `.corr()` (Pearson)
   - Stack → rows of `(symbol_1, symbol_2, corr_value)`
   - Keep only pairs where `symbol_1 < symbol_2`
   - Add `period` column
5. Concatenate both DataFrames

**Returns:** DataFrame with columns `symbol_1, symbol_2, period, corr_value`

---

## Step 2: `etl/load.py` Functions

### DB connection

```python
def get_connection():
    load_dotenv()
    return psycopg2.connect(host, port, dbname, user, password)  # from .env
```

### Insert functions (all `ON CONFLICT DO NOTHING`)

**`insert_companies(cur, ticker_info) -> dict[str, int]`**
- Inserts symbols; SELECTs back `{symbol: id}` mapping used by all downstream inserts

**`insert_company_details(cur, ticker_info, company_ids)`**
- Inserts name, sector, industry, market_cap keyed by company_id

**`insert_stock_prices(cur, long_df, company_ids) -> int`**
- `ON CONFLICT (company_id, date) DO NOTHING`
- Returns row count

**`insert_correlations(cur, corr_df, company_ids) -> int`**
- `ON CONFLICT (company_id_1, company_id_2, period) DO NOTHING`
- Returns row count

**`archive_correlation_snapshot(cur, corr_df, company_ids)`** *(new)*
- Inserts today's correlation values into `correlation_history` with `snapshot_date = date.today()`
- `ON CONFLICT (company_id_1, company_id_2, period, snapshot_date) DO NOTHING`
- Safe to re-run on the same day (idempotent)

**`log_run(cur, status, rows_inserted, rows_skipped, tickers, duration_sec, error_msg=None)`**
- Inserts into `etl_log`

---

## Step 3: `run()` Orchestrator

```python
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
    conn.commit()  # commit main data first

    archive_correlation_snapshot(cur, corr_df, company_ids)
    conn.commit()  # commit history snapshot

    log_run(cur, 'success', sp_count + corr_count, ..., tickers, duration)
    conn.commit()

    # AI commentary — non-fatal; uses same open connection
    try:
        result = generate_commentary(conn)
        if result:
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  Commentary failed (non-fatal): {e}")

except Exception as e:
    conn.rollback()
    log_run(cur, 'error', 0, 0, tickers, duration, str(e))
    conn.commit()
    raise
finally:
    conn.close()
```

### Key design decisions

- **Commentary is non-fatal**: wrapped in its own try/except; ETL succeeds even if Claude API is unavailable
- **Connection sharing**: `generate_commentary(conn)` receives the open connection so the caller controls commit/rollback; when `conn=None` the agent opens and commits its own connection
- **30-day minimum rule**: commentary is skipped if no `correlation_history` snapshot exists ≥ 30 days before `as_of_date`

---

## AI Commentary Agent: `agent/commentary.py`

```python
BASELINE_DAYS = 30  # calendar days

def generate_commentary(conn=None, as_of_date: date = None) -> dict | None:
    # 1. Skip if ANTHROPIC_API_KEY not set
    # 2. Find baseline snapshot ≥ 30 days before as_of_date
    # 3. Fetch current + baseline rows from correlation_history
    # 4. Build delta lines: sym1/sym2 (period): base → curr (Δ)
    # 5. Call claude-sonnet-4-6, max_tokens=150
    # 6. INSERT INTO correlation_alerts (corr_date, baseline_date, commentary)
    # 7. Return {corr_date, baseline_date, commentary}
```

**Prompt structure:**
- Under 100 words
- Three sections: key changes (largest |Δ|), outliers, overall trend
- No raw numbers quoted

---

## Backfill Script: `etl/backfill_history.py`

One-time script to populate `correlation_history` from existing `stock_prices` data.

```bash
python etl/backfill_history.py --months 6 --interval 7
```

Generates weekly snapshots going back N months; skips dates already present (idempotent).

**Run result:** 520 rows inserted (26 weekly snapshots: 2025-12-05 → 2026-06-03)

---

## Verification (current state)

```sql
SELECT count(*) FROM companies;          -- 6 (AAPL, GOOGL, AVGO, ARM, TSM, MSFT)
SELECT count(*) FROM stock_prices;       -- 6,956 (~5y history, ON CONFLICT skips dupes)
SELECT count(*) FROM correlations;       -- 30 (15 pairs × 2 periods)
SELECT count(*) FROM correlation_history;-- 550 (backfill + ongoing ETL runs)
SELECT count(*) FROM etl_log;            -- 9
SELECT * FROM etl_log ORDER BY run_at DESC LIMIT 1;  -- status='success'
```

Re-running `python3 etl/load.py` is safe — all inserts are idempotent. Row counts stay the same except `etl_log` gains one row and `correlation_history` gains one row (today's snapshot, if not already present).
