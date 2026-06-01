# Table Preview

Live snapshot of all 5 tables as of the most recent ETL run (2026-06-01).
Row counts: **5** companies · **5** company_details · **1,255** stock_prices · **20** correlations · **5** etl_log entries.

---

## Relationships at a glance

```
companies (1) ──────────── (1) company_details
    │
    │ (1)
    │
    ├──────────────────────── (N) stock_prices
    │
    └── (1) company_id_1 ─┐
                           ├── correlations
        (1) company_id_2 ─┘

etl_log  (standalone — no FK relationships)
```

---

## 1. `companies`

The root/parent table. Every other domain table foreign-keys back here.

**Data**

| id | symbol |
|----|--------|
| 2  | GOOGL  |
| 3  | AVGO   |
| 5  | TSM    |
| 6  | NVDA   |
| 21 | ARM    |

> IDs are non-sequential because rows were inserted across multiple ETL runs (AAPL replaced by NVDA; MSFT briefly added and removed). Surrogate keys are intentionally stable — gaps are normal and expected.

**Keys**
| Type    | Column   | Detail                                      |
|---------|----------|---------------------------------------------|
| Primary | `id`     | `SERIAL` — auto-increment surrogate key     |
| Unique  | `symbol` | Enforces one row per ticker symbol          |

**Constraints**
| Column   | Constraint | Rule                  |
|----------|------------|-----------------------|
| `symbol` | `NOT NULL` | Every company needs a ticker |
| `symbol` | `UNIQUE`   | No duplicate tickers  |

**Relationships**
| Related table     | Type | Via                            |
|-------------------|------|--------------------------------|
| `company_details` | 1:1  | `company_details.id → companies.id` |
| `stock_prices`    | 1:N  | `stock_prices.company_id → companies.id` |
| `correlations`    | 1:N  | `correlations.company_id_1` and `company_id_2 → companies.id` |

**Normalization**
Ticker symbol is separated from descriptive metadata (name, sector, market cap) into its own table. This keeps the parent narrow and stable — if a symbol is reassigned (e.g. FB → META), only the `symbol` column changes while the integer `id` and all child rows remain untouched.

---

## 2. `company_details`

Extends `companies` with descriptive metadata. Modelled as a separate 1:1 table rather than extra columns on `companies` to keep the parent table focused on identity.

**Data**

| id | company_name                                        | sector                 | industry                       | market_cap        |
|----|-----------------------------------------------------|------------------------|--------------------------------|-------------------|
| 2  | Alphabet Inc.                                       | Communication Services | Internet Content & Information | 4,550,075,875,328 |
| 3  | Broadcom Inc.                                       | Technology             | Semiconductors                 | 2,184,457,093,120 |
| 5  | Taiwan Semiconductor Manufacturing Company Limited  | Technology             | Semiconductors                 | 2,317,576,044,544 |
| 6  | NVIDIA Corporation                                  | Technology             | Semiconductors                 | 5,362,526,715,904 |
| 21 | Arm Holdings plc                                    | Technology             | Semiconductors                 |   437,057,847,296 |

**Keys**
| Type           | Column | Detail                                                         |
|----------------|--------|----------------------------------------------------------------|
| Primary        | `id`   | Shared PK — same value as `companies.id`, not a separate SERIAL |
| Foreign → Primary | `id` | `REFERENCES companies(id)` — `id` is both PK and FK          |

**Constraints**
| Column | Constraint    | Rule                                          |
|--------|---------------|-----------------------------------------------|
| `id`   | `PRIMARY KEY` | Uniqueness guaranteed — only one detail row per company |
| `id`   | `REFERENCES`  | Row cannot exist without a matching `companies` row |

**Relationships**
| Related table | Type | Direction                          |
|---------------|------|------------------------------------|
| `companies`   | 1:1  | `id` is both PK and FK — the shared-key pattern structurally prevents more than one `company_details` row per company |

**Normalization**
Descriptive attributes (`sector`, `industry`, `market_cap`) are non-key facts about the company entity, not about the ticker symbol or price history. Placing them in a separate table is a 2NF decomposition: `companies` handles identity, `company_details` handles metadata that changes infrequently and doesn't belong in the time-series tables.

---

## 3. `stock_prices`

Time-series table. One row per company per trading day — the core dataset from which all correlations are derived.

**Data — earliest date (2025-06-02)**

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 252  | GOOGL  | 2025-06-02 | 167.8400 | 169.8700 | 167.3900 | 169.0300 | 168.4459  | 38,612,300  |
| 503  | AVGO   | 2025-06-02 | 243.2500 | 250.0000 | 243.1900 | 248.7100 | 246.7110  | 19,197,000  |
| 1005 | TSM    | 2025-06-02 | 193.0400 | 195.1600 | 192.2000 | 194.8400 | 192.4563  |  7,447,400  |
| 1256 | NVDA   | 2025-06-02 | 135.4900 | 138.1200 | 135.4000 | 137.3800 | 137.3476  | 197,663,100 |
| 5021 | ARM    | 2025-06-02 | 124.8000 | 127.4800 | 123.5880 | 126.0550 | 126.0550  |  3,297,500  |

**Data — latest date (2026-06-01)**

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 502  | GOOGL  | 2026-06-01 | 376.6000 | 377.7050 | 373.5200 | 375.5600 | 375.5600  | 10,987,872  |
| 753  | AVGO   | 2026-06-01 | 450.1850 | 463.6200 | 442.2400 | 461.3350 | 461.3350  | 15,820,037  |
| 1255 | TSM    | 2026-06-01 | 424.8800 | 447.1500 | 422.6300 | 446.8500 | 446.8500  | 11,425,057  |
| 1506 | NVDA   | 2026-06-01 | 215.7800 | 222.4000 | 215.7000 | 221.3999 | 221.3999  | 124,254,884 |
| 5271 | ARM    | 2026-06-01 | 389.9500 | 421.6899 | 381.2500 | 409.2000 | 409.2000  | 16,486,393  |

> 1,255 rows total — 251 trading days × 5 tickers. `adj_close` differs from `close` on earlier dates where splits or dividends occurred. Returns and correlations are always computed from `adj_close`.

**Keys**
| Type    | Column                  | Detail                                               |
|---------|-------------------------|------------------------------------------------------|
| Primary | `id`                    | `SERIAL` — surrogate key                             |
| Foreign | `company_id`            | `REFERENCES companies(id)` — links row to its ticker |
| Unique  | `(company_id, date)`    | Composite — one price record per ticker per day; also the `ON CONFLICT` target for idempotent inserts |

**Indexes**
| Index name                    | Column       | Purpose                                                      |
|-------------------------------|--------------|--------------------------------------------------------------|
| `idx_stock_prices_company_id` | `company_id` | Fast retrieval of all rows for a given ticker                |
| `idx_stock_prices_date`       | `date`       | Fast date-range scans used by all dashboard chart queries    |

**Constraints**
| Column       | Constraint   | Rule                                              |
|--------------|--------------|---------------------------------------------------|
| `company_id` | `NOT NULL`   | Every price row must belong to a company          |
| `company_id` | `REFERENCES` | Company must exist in `companies` before inserting |
| `date`       | `NOT NULL`   | Every price row must have a date                  |
| `(company_id, date)` | `UNIQUE` | No duplicate rows for the same ticker + day    |

**Relationships**
| Related table | Type | Direction                                        |
|---------------|------|--------------------------------------------------|
| `companies`   | N:1  | Many price rows belong to one company (`company_id → companies.id`) |

**Normalization**
`adj_close` is stored alongside `close` rather than computed on demand. Adjusted close accounts for stock splits and dividends; raw close reflects the actual market price. Storing both avoids re-fetching from yfinance and ensures correlation calculations are reproducible. All OHLCV fields are attributes of the `(company, date)` fact — no partial dependencies, satisfying 2NF and 3NF.

---

## 4. `correlations`

Stores precomputed pairwise Pearson correlations between companies by period. A self-join on `companies` — both FK columns point to the same parent table.

**Data — all 20 rows**

| id | symbol_1 | symbol_2 | period | corr_value | calculated_at       |
|----|----------|----------|--------|------------|---------------------|
|  8 | AVGO     | GOOGL    | 1m     |  0.0370    | 2026-06-01 19:39:14 |
|  9 | AVGO     | TSM      | 1m     |  0.3885    | 2026-06-01 19:39:14 |
| 10 | GOOGL    | TSM      | 1m     |  0.3277    | 2026-06-01 19:39:14 |
| 26 | AVGO     | NVDA     | 1m     |  0.3567    | 2026-06-01 19:39:14 |
| 28 | GOOGL    | NVDA     | 1m     |  0.3090    | 2026-06-01 19:39:14 |
| 30 | NVDA     | TSM      | 1m     |  **0.7316**| 2026-06-01 19:39:14 |
| 71 | ARM      | AVGO     | 1m     |  0.3872    | 2026-06-01 19:39:14 |
| 72 | ARM      | GOOGL    | 1m     |  0.2178    | 2026-06-01 19:39:14 |
| 73 | ARM      | NVDA     | 1m     |  0.3990    | 2026-06-01 19:39:14 |
| 74 | ARM      | TSM      | 1m     |  0.5859    | 2026-06-01 19:39:14 |
| 18 | AVGO     | GOOGL    | 6m     |  0.3603    | 2026-06-01 19:39:14 |
| 19 | AVGO     | TSM      | 6m     |  0.5616    | 2026-06-01 19:39:14 |
| 20 | GOOGL    | TSM      | 6m     |  0.3988    | 2026-06-01 19:39:14 |
| 36 | AVGO     | NVDA     | 6m     |  0.5255    | 2026-06-01 19:39:14 |
| 38 | GOOGL    | NVDA     | 6m     |  0.2472    | 2026-06-01 19:39:14 |
| 40 | NVDA     | TSM      | 6m     |  0.6410    | 2026-06-01 19:39:14 |
| 81 | ARM      | AVGO     | 6m     |  0.3720    | 2026-06-01 19:39:14 |
| 82 | ARM      | GOOGL    | 6m     |  0.2435    | 2026-06-01 19:39:14 |
| 83 | ARM      | NVDA     | 6m     |  0.3763    | 2026-06-01 19:39:14 |
| 84 | ARM      | TSM      | 6m     |  0.5134    | 2026-06-01 19:39:14 |

> Strongest 1m pair: **NVDA / TSM (r = 0.73)**. Weakest: **AVGO / GOOGL (r = 0.04)**. IDs are non-sequential — rows were upserted across multiple runs as tickers changed.

**Keys**
| Type    | Column                              | Detail                                                              |
|---------|-------------------------------------|---------------------------------------------------------------------|
| Primary | `id`                                | `SERIAL` — surrogate key                                           |
| Foreign | `company_id_1`                      | `REFERENCES companies(id)` — first member of the pair              |
| Foreign | `company_id_2`                      | `REFERENCES companies(id)` — second member of the pair             |
| Unique  | `(company_id_1, company_id_2, period)` | One correlation value per ordered pair per period; `ON CONFLICT` target for upserts |

**Indexes**
| Index name                      | Column         | Purpose                                                           |
|---------------------------------|----------------|-------------------------------------------------------------------|
| `idx_correlations_company_id_1` | `company_id_1` | Fast lookup of all pairs where a ticker is the first member       |
| `idx_correlations_company_id_2` | `company_id_2` | Fast lookup of all pairs where a ticker is the second member      |
| `idx_correlations_period`       | `period`       | Fast filtering by period (e.g. all 1m rows for the heatmap query) |

**Constraints**
| Column / Expression              | Constraint   | Rule                                                        |
|----------------------------------|--------------|-------------------------------------------------------------|
| `company_id_1`                   | `NOT NULL`   | A correlation row must always reference two companies       |
| `company_id_2`                   | `NOT NULL`   | —                                                           |
| `company_id_1`                   | `REFERENCES` | First company must exist in `companies`                     |
| `company_id_2`                   | `REFERENCES` | Second company must exist in `companies`                    |
| `period`                         | `NOT NULL`   | Period is always required                                   |
| `period IN ('1m', '6m')`         | `CHECK`      | Only the two supported windows are accepted at the DB level |
| `corr_value BETWEEN -1 AND 1`    | `CHECK`      | Enforces valid Pearson r range — invalid values are rejected |
| `(company_id_1, company_id_2, period)` | `UNIQUE` | No duplicate pair+period rows                             |

**Relationships**
| Related table | Type | Direction                                                            |
|---------------|------|----------------------------------------------------------------------|
| `companies`   | N:1 (self-join) | Both `company_id_1` and `company_id_2` reference `companies.id`. One company can appear in many correlation rows — as the first or second member of any pair. |

**Normalization**
Pairs are stored in one direction only (`symbol_1 < symbol_2` enforced at the ETL layer) to avoid storing symmetric duplicates — Pearson r is symmetric, so r(A,B) = r(B,A). The dashboard reconstructs the full symmetric matrix at render time. `calculated_at` records when the value was last computed, making it possible to detect stale rows if the ETL schedule changes.

---

## 5. `etl_log`

Audit log for every pipeline run. Standalone — no foreign keys by design so a failed run's log entry is never rolled back with the failed transaction.

**Data — all 5 rows**

| id | run_at              | status  | rows_inserted | rows_skipped | tickers                      | duration_sec | error_msg |
|----|---------------------|---------|---------------|--------------|------------------------------|--------------|-----------|
| 1  | 2026-06-01 18:57:14 | success | 75            | 1,200        | AAPL,GOOGL,AVGO,ARM,TSM      | 2.18         | —         |
| 2  | 2026-06-01 19:18:55 | success | 20            | 1,255        | NVDA,GOOGL,AVGO,ARM,TSM      | 2.32         | —         |
| 3  | 2026-06-01 19:33:31 | success | 36            | 1,500        | ARM,AVGO,GOOGL,NVDA,TSM,MSFT | 2.77         | —         |
| 4  | 2026-06-01 19:38:44 | success | 75            | 1,200        | AVGO,GOOGL,NVDA,TSM,ARM      | 1.77         | —         |
| 5  | 2026-06-01 19:39:14 | success | 20            | 1,255        | ARM,AVGO,GOOGL,NVDA,TSM      | 1.59         | —         |

**Run history**

| Run | What happened |
|-----|---------------|
| 1   | Initial load — AAPL was the fifth ticker; 75 new rows (55 prices + 20 correlations) |
| 2   | AAPL swapped for NVDA — AAPL deleted, NVDA inserted; 20 correlation upserts |
| 3   | MSFT briefly added via the Manage Tickers tab (6-ticker run); later removed |
| 4   | Refresh after MSFT removal — 75 rows re-upserted for the final 5-ticker set |
| 5   | Final correlation upsert after ticker cleanup — 20 rows, all current |

**Keys**
| Type    | Column  | Detail                          |
|---------|---------|---------------------------------|
| Primary | `id`    | `SERIAL` — surrogate key        |

**Constraints**
| Column   | Constraint   | Rule                                     |
|----------|--------------|------------------------------------------|
| `status` | `NOT NULL`   | Every run must record a status           |
| `rows_inserted` | `DEFAULT 0` | Defaults to 0 if not explicitly set |
| `rows_skipped`  | `DEFAULT 0` | —                                   |
| `run_at` | `DEFAULT NOW()` | Automatically timestamped on insert   |

**Relationships**
None. `etl_log` has no foreign keys. A failed ETL run still writes a row here even if no company or price data was committed — decoupling the log from transactional data ensures the audit trail is never lost to a rollback.

**Normalization**
`tickers` is stored as a comma-separated `TEXT` field rather than a normalized junction table. The log is append-only and read purely for display — the overhead of a separate join table has no benefit here, and the denormalized field keeps each log row fully self-contained for human review.
