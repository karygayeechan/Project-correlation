# Correlation Regime Alerts & Commentary Agent

## What this does

After each ETL run, this agent compares the latest pairwise correlation values against a snapshot from ~30 days ago and asks Claude to write a plain-English summary of what changed and what it might mean.

Example output:
> "NVDA–AVGO correlation tightened significantly over the past month, suggesting a sector-wide move rather than stock-specific news. GOOGL has diverged from the group on the 1m window, which may reflect idiosyncratic ad-revenue dynamics. The 6m picture remains broadly correlated across all names."

Alerts only fire once ≥ 30 days of history exists. Sub-month comparisons are skipped intentionally — short windows are too noisy to interpret meaningfully.

---

## Files added or changed

### New files

#### `agent/__init__.py`
Empty. Makes `agent/` a Python package so `from agent.commentary import ...` works.

#### `agent/commentary.py`
The agent itself. Steps it performs:
1. Checks `ANTHROPIC_API_KEY` is set — skips silently if not.
2. Queries `correlation_history` for the latest snapshot on or before `today - 30 days`. If none exists, skips.
3. Fetches the current snapshot (today) and the baseline snapshot.
4. Builds a delta table: for every pair/period, computes `current_value - baseline_value`.
5. Constructs a prompt describing the comparison window and the delta lines.
6. Calls `claude-sonnet-4-6` with `max_tokens=350`.
7. Inserts the generated commentary into `correlation_alerts`.
8. Returns a dict `{corr_date, baseline_date, commentary}` to the caller.

Key design choices:
- Accepts an optional `conn` argument. When called from `etl/load.py` the existing connection is reused (no extra connection overhead, commit is the caller's responsibility). When called standalone it opens and closes its own connection.
- The `anthropic` SDK is imported lazily inside the function so the module loads fine even if the package is missing.

---

### Modified files

#### `db/schema.sql`
Two new tables appended:

**`correlation_history`**
Stores one row per pair / period / day. The `UNIQUE` constraint on `(company_id_1, company_id_2, period, snapshot_date)` makes re-running the ETL on the same day idempotent.
```
id, company_id_1, company_id_2, period, corr_value, snapshot_date
```

**`correlation_alerts`**
Stores each generated commentary with the dates it compared.
```
id, generated_at, corr_date, baseline_date, commentary
```

Note: the column is named `corr_date` (not `current_date`) because `current_date` is a reserved PostgreSQL keyword.

#### `etl/load.py`
Three additions:

1. **`sys.path` fix at the top** — adds the project root so `from agent.commentary import generate_commentary` resolves correctly.

2. **`archive_correlation_snapshot(cur, corr_df, company_ids)`** — copies the just-computed `corr_df` rows into `correlation_history` with today's date. Uses `ON CONFLICT DO NOTHING` so it is safe to call multiple times per day.

3. **Two new steps inside `run()`**, inserted after the main `conn.commit()`:
   ```
   archive_correlation_snapshot(cur, corr_df, company_ids)
   conn.commit()

   generate_commentary(conn)   ← wrapped in try/except
   conn.commit()               ← only if commentary succeeded
   ```
   Commentary failures are non-fatal: a `conn.rollback()` is issued and the ETL continues to `log_run` and its final commit.

#### `app/db.py`
Added `get_alerts(limit)` — queries `correlation_alerts ORDER BY generated_at DESC`.

#### `api/main.py`
Added `GET /alerts` endpoint — calls `db.get_alerts(limit)` and returns a list of records.

#### `app/api_client.py`
Added `get_alerts(limit)` — calls `GET /alerts` and returns a DataFrame.

#### `app/streamlit_app.py`
- Added `_alerts()` cached wrapper (TTL 300s).
- Added **"Regime Alerts"** tab between Volatility and Manage Tickers.
- Tab shows: latest alert text prominently + metric chips for the two dates compared; a full history table if more than one alert exists; an info banner if no alerts exist yet.

---

## Data flow on each ETL run

```
etl/load.py run()
  │
  ├─ fetch_raw / reshape / compute_correlations
  ├─ insert companies, prices, correlations  ──► conn.commit()
  │
  ├─ archive_correlation_snapshot()          ──► correlation_history  ──► conn.commit()
  │
  └─ generate_commentary()
        ├─ query correlation_history (today vs. today - 30d)
        ├─ if no baseline → skip
        ├─ build delta prompt → call claude-sonnet-4-6
        ├─ INSERT into correlation_alerts
        └─ return result                     ──► conn.commit()  (caller)
```

---

## Environment variable required

```
ANTHROPIC_API_KEY=sk-ant-...
```

Add to `.env` at the project root. Without it every ETL run prints `Skipping commentary — ANTHROPIC_API_KEY not set` and continues normally.

---

## How to test once 30 days of history exists

```bash
# Manually backfill a fake baseline snapshot (30+ days ago) for testing:
psql -d postgres -c "
  INSERT INTO correlation_history (company_id_1, company_id_2, period, corr_value, snapshot_date)
  SELECT company_id_1, company_id_2, period, corr_value, CURRENT_DATE - 31
  FROM correlations
  ON CONFLICT DO NOTHING;
"

# Then run the ETL — commentary should fire:
python3 etl/load.py
```
