# Tables and Metadata

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

### Data

| id | symbol |
|----|--------|
| 2  | GOOGL  |
| 3  | AVGO   |
| 5  | TSM    |
| 6  | NVDA   |
| 21 | ARM    |

> IDs are non-sequential because rows were inserted across multiple ETL runs (AAPL replaced by NVDA; MSFT briefly added and removed). Surrogate keys are intentionally stable — gaps are normal and expected.

### Column Metadata

| Column   | Data Type           | Max Length | Nullable | Default                         |
|----------|---------------------|------------|----------|---------------------------------|
| `id`     | `integer`           | —          | NO       | `nextval('companies_id_seq')`   |
| `symbol` | `character varying` | 10         | NO       | —                               |

### Storage Metadata

| Metric        | Value       |
|---------------|-------------|
| Row count     | 5           |
| Table size    | 8,192 bytes |
| Indexes size  | 32 kB       |
| Total size    | 40 kB       |
| Total inserts | 8           |
| Total updates | 0           |
| Total deletes | 3           |

### Indexes

| Index name             | Columns  | Unique | Primary |
|------------------------|----------|--------|---------|
| `companies_pkey`       | `id`     | Yes    | Yes     |
| `companies_symbol_key` | `symbol` | Yes    | No      |

### Keys & Constraints

| Type        | Column            | Rule                                      |
|-------------|-------------------|-------------------------------------------|
| Primary key | `id`              | `SERIAL` auto-increment surrogate key     |
| Unique      | `symbol`          | One row per ticker symbol                 |
| NOT NULL    | `id`, `symbol`    | Both columns are required                 |

### Relationships

| Related table     | Type | Via                                          |
|-------------------|------|----------------------------------------------|
| `company_details` | 1:1  | `company_details.id → companies.id`          |
| `stock_prices`    | 1:N  | `stock_prices.company_id → companies.id`     |
| `correlations`    | 1:N  | `correlations.company_id_1/2 → companies.id` |

### Normalization
Ticker symbol is separated from descriptive metadata (name, sector, market cap) into its own table. This keeps the parent narrow and stable — if a symbol is reassigned (e.g. FB → META), only `symbol` changes while the integer `id` and all child rows remain untouched.

---

## 2. `company_details`

Extends `companies` with descriptive metadata. Modelled as a separate 1:1 table rather than extra columns on `companies` to keep the parent table focused on identity.

### Data

| id | company_name                                        | sector                 | industry                       | market_cap        |
|----|-----------------------------------------------------|------------------------|--------------------------------|-------------------|
| 2  | Alphabet Inc.                                       | Communication Services | Internet Content & Information | 4,550,075,875,328 |
| 3  | Broadcom Inc.                                       | Technology             | Semiconductors                 | 2,184,457,093,120 |
| 5  | Taiwan Semiconductor Manufacturing Company Limited  | Technology             | Semiconductors                 | 2,317,576,044,544 |
| 6  | NVIDIA Corporation                                  | Technology             | Semiconductors                 | 5,362,526,715,904 |
| 21 | Arm Holdings plc                                    | Technology             | Semiconductors                 |   437,057,847,296 |

### Column Metadata

| Column         | Data Type           | Max Length | Nullable | Default |
|----------------|---------------------|------------|----------|---------|
| `id`           | `integer`           | —          | NO       | —       |
| `company_name` | `character varying` | 255        | YES      | —       |
| `sector`       | `character varying` | 100        | YES      | —       |
| `industry`     | `character varying` | 100        | YES      | —       |
| `market_cap`   | `bigint`            | —          | YES      | —       |

### Storage Metadata

| Metric        | Value       |
|---------------|-------------|
| Row count     | 5           |
| Table size    | 8,192 bytes |
| Indexes size  | 16 kB       |
| Total size    | 24 kB       |
| Total inserts | 8           |
| Total updates | 0           |
| Total deletes | 3           |

### Indexes

| Index name             | Columns | Unique | Primary |
|------------------------|---------|--------|---------|
| `company_details_pkey` | `id`    | Yes    | Yes     |

### Keys & Constraints

| Type        | Column | Rule                                                          |
|-------------|--------|---------------------------------------------------------------|
| Primary key | `id`   | Shared PK — same value as `companies.id`, not a new `SERIAL` |
| Foreign key | `id`   | `REFERENCES companies(id)` — `id` is both PK and FK          |
| NOT NULL    | `id`   | Required                                                      |

### Relationships

| Related table | Type | Direction                                                                           |
|---------------|------|-------------------------------------------------------------------------------------|
| `companies`   | 1:1  | `id` is both PK and FK — the shared-key pattern structurally prevents more than one `company_details` row per company |

### Normalization
Descriptive attributes (`sector`, `industry`, `market_cap`) are non-key facts about the company entity, not about the ticker symbol or price history. Placing them here is a 2NF decomposition: `companies` handles identity, `company_details` handles metadata that changes infrequently. The shared PK guarantees the 1:1 — there is no way to insert a second detail row for the same company.

---

## 3. `stock_prices`

Time-series table. One row per company per trading day — the core dataset from which all correlations are derived.

### Data — earliest date (2025-06-02)

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 252  | GOOGL  | 2025-06-02 | 167.8400 | 169.8700 | 167.3900 | 169.0300 | 168.4459  | 38,612,300  |
| 503  | AVGO   | 2025-06-02 | 243.2500 | 250.0000 | 243.1900 | 248.7100 | 246.7110  | 19,197,000  |
| 1005 | TSM    | 2025-06-02 | 193.0400 | 195.1600 | 192.2000 | 194.8400 | 192.4563  |  7,447,400  |
| 1256 | NVDA   | 2025-06-02 | 135.4900 | 138.1200 | 135.4000 | 137.3800 | 137.3476  | 197,663,100 |
| 5021 | ARM    | 2025-06-02 | 124.8000 | 127.4800 | 123.5880 | 126.0550 | 126.0550  |  3,297,500  |

### Data — latest date (2026-06-01)

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 502  | GOOGL  | 2026-06-01 | 376.6000 | 377.7050 | 373.5200 | 375.5600 | 375.5600  | 10,987,872  |
| 753  | AVGO   | 2026-06-01 | 450.1850 | 463.6200 | 442.2400 | 461.3350 | 461.3350  | 15,820,037  |
| 1255 | TSM    | 2026-06-01 | 424.8800 | 447.1500 | 422.6300 | 446.8500 | 446.8500  | 11,425,057  |
| 1506 | NVDA   | 2026-06-01 | 215.7800 | 222.4000 | 215.7000 | 221.3999 | 221.3999  | 124,254,884 |
| 5271 | ARM    | 2026-06-01 | 389.9500 | 421.6899 | 381.2500 | 409.2000 | 409.2000  | 16,486,393  |

> 1,255 rows total — 251 trading days × 5 tickers. `adj_close` differs from `close` on earlier dates where splits or dividends occurred. Returns and correlations are always computed from `adj_close`.

### Column Metadata

| Column       | Data Type           | Precision / Scale | Nullable | Default                              |
|--------------|---------------------|-------------------|----------|--------------------------------------|
| `id`         | `integer`           | 32-bit            | NO       | `nextval('stock_prices_id_seq')`     |
| `company_id` | `integer`           | 32-bit            | NO       | —                                    |
| `date`       | `date`              | —                 | NO       | —                                    |
| `open`       | `numeric`           | 12 digits, 4 dec  | YES      | —                                    |
| `high`       | `numeric`           | 12 digits, 4 dec  | YES      | —                                    |
| `low`        | `numeric`           | 12 digits, 4 dec  | YES      | —                                    |
| `close`      | `numeric`           | 12 digits, 4 dec  | YES      | —                                    |
| `adj_close`  | `numeric`           | 12 digits, 4 dec  | YES      | —                                    |
| `volume`     | `bigint`            | 64-bit            | YES      | —                                    |

### Storage Metadata

| Metric        | Value   |
|---------------|---------|
| Row count     | 1,255   |
| Table size    | 128 kB  |
| Indexes size  | 184 kB  |
| Total size    | 344 kB  |
| Total inserts | 2,008   |
| Total updates | 0       |
| Total deletes | 753     |

> Indexes are larger than the table itself (184 kB vs 128 kB) because three indexes cover this table — a common pattern for heavily-queried time-series data.

### Indexes

| Index name                         | Columns              | Unique | Primary |
|------------------------------------|----------------------|--------|---------|
| `stock_prices_pkey`                | `id`                 | Yes    | Yes     |
| `stock_prices_company_id_date_key` | `company_id, date`   | Yes    | No      |
| `idx_stock_prices_company_id`      | `company_id`         | No     | No      |
| `idx_stock_prices_date`            | `date`               | No     | No      |

### Keys & Constraints

| Type        | Column                 | Rule                                                             |
|-------------|------------------------|------------------------------------------------------------------|
| Primary key | `id`                   | `SERIAL` surrogate key                                           |
| Foreign key | `company_id`           | `REFERENCES companies(id)` — company must exist before inserting |
| Unique      | `(company_id, date)`   | One price record per ticker per day; `ON CONFLICT` target        |
| NOT NULL    | `company_id`, `date`   | Both required on every row                                       |

### Relationships

| Related table | Type | Direction                                                            |
|---------------|------|----------------------------------------------------------------------|
| `companies`   | N:1  | Many price rows belong to one company (`company_id → companies.id`) |

### Normalization
All OHLCV columns are attributes of the `(company, date)` fact — no partial dependencies, satisfying 2NF and 3NF. `adj_close` is stored alongside `close` rather than computed on demand because adjusted prices require the full corporate action history from yfinance; storing both ensures reproducibility and avoids re-fetching.

---

## 4. `correlations`

Stores precomputed pairwise Pearson correlations between companies by period. A self-join on `companies` — both FK columns point to the same parent table.

### Data — all 20 rows

| id | symbol_1 | symbol_2 | period | corr_value  | calculated_at       |
|----|----------|----------|--------|-------------|---------------------|
|  8 | AVGO     | GOOGL    | 1m     | 0.0370      | 2026-06-01 19:39:14 |
|  9 | AVGO     | TSM      | 1m     | 0.3885      | 2026-06-01 19:39:14 |
| 10 | GOOGL    | TSM      | 1m     | 0.3277      | 2026-06-01 19:39:14 |
| 26 | AVGO     | NVDA     | 1m     | 0.3567      | 2026-06-01 19:39:14 |
| 28 | GOOGL    | NVDA     | 1m     | 0.3090      | 2026-06-01 19:39:14 |
| 30 | NVDA     | TSM      | 1m     | **0.7316**  | 2026-06-01 19:39:14 |
| 71 | ARM      | AVGO     | 1m     | 0.3872      | 2026-06-01 19:39:14 |
| 72 | ARM      | GOOGL    | 1m     | 0.2178      | 2026-06-01 19:39:14 |
| 73 | ARM      | NVDA     | 1m     | 0.3990      | 2026-06-01 19:39:14 |
| 74 | ARM      | TSM      | 1m     | 0.5859      | 2026-06-01 19:39:14 |
| 18 | AVGO     | GOOGL    | 6m     | 0.3603      | 2026-06-01 19:39:14 |
| 19 | AVGO     | TSM      | 6m     | 0.5616      | 2026-06-01 19:39:14 |
| 20 | GOOGL    | TSM      | 6m     | 0.3988      | 2026-06-01 19:39:14 |
| 36 | AVGO     | NVDA     | 6m     | 0.5255      | 2026-06-01 19:39:14 |
| 38 | GOOGL    | NVDA     | 6m     | 0.2472      | 2026-06-01 19:39:14 |
| 40 | NVDA     | TSM      | 6m     | 0.6410      | 2026-06-01 19:39:14 |
| 81 | ARM      | AVGO     | 6m     | 0.3720      | 2026-06-01 19:39:14 |
| 82 | ARM      | GOOGL    | 6m     | 0.2435      | 2026-06-01 19:39:14 |
| 83 | ARM      | NVDA     | 6m     | 0.3763      | 2026-06-01 19:39:14 |
| 84 | ARM      | TSM      | 6m     | 0.5134      | 2026-06-01 19:39:14 |

> Strongest 1m pair: **NVDA / TSM (r = 0.73)**. Weakest: **AVGO / GOOGL (r = 0.04)**. IDs are non-sequential — rows were upserted across multiple runs as tickers changed.

### Column Metadata

| Column          | Data Type                     | Precision / Scale | Nullable | Default                              |
|-----------------|-------------------------------|-------------------|----------|--------------------------------------|
| `id`            | `integer`                     | 32-bit            | NO       | `nextval('correlations_id_seq')`     |
| `company_id_1`  | `integer`                     | 32-bit            | NO       | —                                    |
| `company_id_2`  | `integer`                     | 32-bit            | NO       | —                                    |
| `period`        | `character varying`           | max 10 chars      | NO       | —                                    |
| `corr_value`    | `numeric`                     | 6 digits, 4 dec   | YES      | —                                    |
| `calculated_at` | `timestamp without time zone` | —                 | YES      | `now()`                              |

### Storage Metadata

| Metric        | Value       |
|---------------|-------------|
| Row count     | 20          |
| Table size    | 8,192 bytes |
| Indexes size  | 80 kB       |
| Total size    | 120 kB      |
| Total inserts | 46          |
| Total updates | 64          |
| Total deletes | 26          |

> High update count (64) relative to row count (20) reflects repeated upserts across ETL runs — each refresh rewrites `corr_value` and `calculated_at` on all 20 rows via `ON CONFLICT DO UPDATE`.

### Indexes

| Index name                                          | Columns                              | Unique | Primary |
|-----------------------------------------------------|--------------------------------------|--------|---------|
| `correlations_pkey`                                 | `id`                                 | Yes    | Yes     |
| `correlations_company_id_1_company_id_2_period_key` | `company_id_1, company_id_2, period` | Yes    | No      |
| `idx_correlations_company_id_1`                     | `company_id_1`                       | No     | No      |
| `idx_correlations_company_id_2`                     | `company_id_2`                       | No     | No      |
| `idx_correlations_period`                           | `period`                             | No     | No      |

### Keys & Constraints

| Type        | Column / Expression                    | Rule                                                        |
|-------------|----------------------------------------|-------------------------------------------------------------|
| Primary key | `id`                                   | `SERIAL` surrogate key                                      |
| Foreign key | `company_id_1`                         | `REFERENCES companies(id)`                                  |
| Foreign key | `company_id_2`                         | `REFERENCES companies(id)`                                  |
| Unique      | `(company_id_1, company_id_2, period)` | One value per ordered pair per period; `ON CONFLICT` target |
| NOT NULL    | `company_id_1`, `company_id_2`, `period` | All three required                                        |
| CHECK       | `period IN ('1m', '6m')`               | Rejects any unsupported window at the DB level              |
| CHECK       | `corr_value BETWEEN -1 AND 1`          | Enforces valid Pearson r range                              |

### Relationships

| Related table | Type            | Direction                                                                                                           |
|---------------|-----------------|---------------------------------------------------------------------------------------------------------------------|
| `companies`   | N:1 (self-join) | Both `company_id_1` and `company_id_2` reference `companies.id`. One company can appear in many correlation rows as either member of a pair. |

### Normalization
Pairs are stored in one direction only (`symbol_1 < symbol_2` alphabetically, enforced at the ETL layer) to avoid symmetric duplicates — Pearson r is symmetric so r(A,B) = r(B,A). The dashboard reconstructs the full symmetric matrix at render time. `calculated_at` records when the value was last written, making it easy to detect stale rows if the ETL schedule changes.

---

## 5. `etl_log`

Audit log for every pipeline run. Standalone — no foreign keys by design so a failed run's log entry is never rolled back with the failed transaction.

### Data — all 5 rows

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

### Column Metadata

| Column          | Data Type                     | Precision / Scale | Nullable | Default                       |
|-----------------|-------------------------------|-------------------|----------|-------------------------------|
| `id`            | `integer`                     | 32-bit            | NO       | `nextval('etl_log_id_seq')`   |
| `run_at`        | `timestamp without time zone` | —                 | YES      | `now()`                       |
| `status`        | `character varying`           | max 20 chars      | NO       | —                             |
| `rows_inserted` | `integer`                     | 32-bit            | YES      | `0`                           |
| `rows_skipped`  | `integer`                     | 32-bit            | YES      | `0`                           |
| `tickers`       | `text`                        | unlimited         | YES      | —                             |
| `duration_sec`  | `numeric`                     | 8 digits, 2 dec   | YES      | —                             |
| `error_msg`     | `text`                        | unlimited         | YES      | —                             |

### Storage Metadata

| Metric        | Value       |
|---------------|-------------|
| Row count     | 5           |
| Table size    | 8,192 bytes |
| Indexes size  | 16 kB       |
| Total size    | 32 kB       |
| Total inserts | 5           |
| Total updates | 0           |
| Total deletes | 0           |

### Indexes

| Index name     | Columns | Unique | Primary |
|----------------|---------|--------|---------|
| `etl_log_pkey` | `id`    | Yes    | Yes     |

### Keys & Constraints

| Type        | Column          | Rule                                 |
|-------------|-----------------|--------------------------------------|
| Primary key | `id`            | `SERIAL` surrogate key               |
| NOT NULL    | `status`        | Every run must record a status       |
| DEFAULT     | `rows_inserted` | `0` if not explicitly set            |
| DEFAULT     | `rows_skipped`  | `0` if not explicitly set            |
| DEFAULT     | `run_at`        | `NOW()` — auto-timestamped on insert |

### Relationships
None. `etl_log` has no foreign keys. A failed ETL run still writes a row here even if no company or price data was committed — decoupling the log from transactional data ensures the audit trail is never lost to a rollback.

### Normalization
`tickers` is stored as comma-separated `text` rather than a normalized junction table. The log is append-only and read purely for display — the overhead of a separate join table has no benefit here, and the denormalized field keeps each log row fully self-contained for human review.
