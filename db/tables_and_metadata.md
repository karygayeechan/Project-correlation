# Tables and Metadata

Live snapshot of all 7 tables as of the most recent ETL run (2026-06-03).
Row counts: **6** companies · **6** company_details · **6,956** stock_prices · **30** correlations · **550** correlation_history · **4** correlation_alerts · **9** etl_log entries.

---

## Relationships at a glance

```
companies (1) ──────────── (1) company_details
    │
    │ (1)
    │
    ├──────────────────────────────── (N) stock_prices
    │
    ├── (1) company_id_1 ─┐
    │                      ├── correlations
    │   (1) company_id_2 ─┘
    │
    ├── (1) company_id_1 ─┐
    │                      ├── correlation_history
    │   (1) company_id_2 ─┘
    │
etl_log              (standalone — no FK relationships)
correlation_alerts   (standalone — no FK relationships)
```

---

## 1. `companies`

The root/parent table. Every other domain table foreign-keys back here.

### Data

| id | symbol |
|----|--------|
|  2 | GOOGL  |
|  3 | AVGO   |
|  5 | TSM    |
|  6 | NVDA   |
| 21 | ARM    |
| 42 | MSFT   |

> IDs are non-sequential because rows were inserted across multiple ETL runs (AAPL replaced by NVDA; MSFT added; ticker churn creates gaps). Surrogate keys are intentionally stable — gaps are normal and expected.

### Column Metadata

| Column   | Data Type           | Max Length | Nullable | Default                         |
|----------|---------------------|------------|----------|---------------------------------|
| `id`     | `integer`           | —          | NO       | `nextval('companies_id_seq')`   |
| `symbol` | `character varying` | 10         | NO       | —                               |

### Indexes

| Index name             | Columns  | Unique | Primary |
|------------------------|----------|--------|---------|
| `companies_pkey`       | `id`     | Yes    | Yes     |
| `companies_symbol_key` | `symbol` | Yes    | No      |

### Keys & Constraints

| Type        | Column         | Rule                                  |
|-------------|----------------|---------------------------------------|
| Primary key | `id`           | `SERIAL` auto-increment surrogate key |
| Unique      | `symbol`       | One row per ticker symbol             |
| NOT NULL    | `id`, `symbol` | Both columns are required             |

### Relationships

| Related table        | Type | Via                                                    |
|----------------------|------|--------------------------------------------------------|
| `company_details`    | 1:1  | `company_details.id → companies.id`                    |
| `stock_prices`       | 1:N  | `stock_prices.company_id → companies.id`               |
| `correlations`       | 1:N  | `correlations.company_id_1/2 → companies.id`           |
| `correlation_history`| 1:N  | `correlation_history.company_id_1/2 → companies.id`    |

### Normalization
Ticker symbol is separated from descriptive metadata into its own table. If a symbol is reassigned (e.g. FB → META), only `symbol` changes while the integer `id` and all child rows remain untouched.

---

## 2. `company_details`

Extends `companies` with descriptive metadata. Modelled as a separate 1:1 table to keep the parent focused on identity.

### Data

| id | company_name                                       | sector                 | industry                       | market_cap        |
|----|----------------------------------------------------|------------------------|--------------------------------|-------------------|
|  2 | Alphabet Inc.                                      | Communication Services | Internet Content & Information | 4,550,075,875,328 |
|  3 | Broadcom Inc.                                      | Technology             | Semiconductors                 | 2,184,457,093,120 |
|  5 | Taiwan Semiconductor Manufacturing Company Limited | Technology             | Semiconductors                 | 2,317,576,044,544 |
|  6 | NVIDIA Corporation                                 | Technology             | Semiconductors                 | 5,362,526,715,904 |
| 21 | Arm Holdings plc                                   | Technology             | Semiconductors                 |   437,057,847,296 |
| 42 | Microsoft Corporation                              | Technology             | Software—Infrastructure        | 3,341,000,000,000 |

### Column Metadata

| Column         | Data Type           | Max Length | Nullable | Default |
|----------------|---------------------|------------|----------|---------|
| `id`           | `integer`           | —          | NO       | —       |
| `company_name` | `character varying` | 255        | YES      | —       |
| `sector`       | `character varying` | 100        | YES      | —       |
| `industry`     | `character varying` | 100        | YES      | —       |
| `market_cap`   | `bigint`            | —          | YES      | —       |

### Keys & Constraints

| Type        | Column | Rule                                                          |
|-------------|--------|---------------------------------------------------------------|
| Primary key | `id`   | Shared PK — same value as `companies.id`, not a new `SERIAL` |
| Foreign key | `id`   | `REFERENCES companies(id)` — `id` is both PK and FK          |
| NOT NULL    | `id`   | Required                                                      |

---

## 3. `stock_prices`

Time-series table. One row per company per trading day — the core dataset from which all correlations are derived. Now covers **5 years** of history fetched via yfinance.

### Data — earliest date (2021-06-03, sample)

| id    | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|-------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 9037  | GOOGL  | 2021-06-03 | 117.2865 | 117.8510 | 116.4760 | 117.3790 |  116.4168 |  18,696,000 |
| 7782  | NVDA   | 2021-06-03 |  16.7008 |  17.2590 |  16.5830 |  16.9697 |   16.9271 | 580,008,000 |
| 12228 | TSM    | 2021-06-03 | 118.0500 | 118.2600 | 116.4600 | 116.8200 |  107.5243 |   4,912,200 |

### Data — latest date (2026-06-02, sample)

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 7279 | GOOGL  | 2026-06-02 | 366.5900 | 373.5400 | 358.4400 | 361.8500 |  361.8500 |  50,181,400 |
| 7530 | NVDA   | 2026-06-02 | 227.1800 | 232.2800 | 221.3500 | 222.8200 |  222.8200 | 192,211,900 |
| 7781 | TSM    | 2026-06-02 | 440.5800 | 448.3800 | 436.0100 | 446.6900 |  446.6900 |  10,092,000 |

> **6,956 rows total.** AVGO, GOOGL, MSFT, NVDA, TSM: 1,255 rows each (2021-06-03 → 2026-06-02). ARM: 681 rows (2023-09-14 → 2026-06-02, IPO date). `adj_close` differs from `close` on dates where splits or dividends occurred; all return calculations use `adj_close`.

### Column Metadata

| Column       | Data Type  | Precision / Scale | Nullable | Default                          |
|--------------|------------|-------------------|----------|----------------------------------|
| `id`         | `integer`  | 32-bit            | NO       | `nextval('stock_prices_id_seq')` |
| `company_id` | `integer`  | 32-bit            | NO       | —                                |
| `date`       | `date`     | —                 | NO       | —                                |
| `open`       | `numeric`  | 12 digits, 4 dec  | YES      | —                                |
| `high`       | `numeric`  | 12 digits, 4 dec  | YES      | —                                |
| `low`        | `numeric`  | 12 digits, 4 dec  | YES      | —                                |
| `close`      | `numeric`  | 12 digits, 4 dec  | YES      | —                                |
| `adj_close`  | `numeric`  | 12 digits, 4 dec  | YES      | —                                |
| `volume`     | `bigint`   | 64-bit            | YES      | —                                |

### Indexes

| Index name                         | Columns            | Unique | Primary |
|------------------------------------|--------------------|--------|---------|
| `stock_prices_pkey`                | `id`               | Yes    | Yes     |
| `stock_prices_company_id_date_key` | `company_id, date` | Yes    | No      |
| `idx_stock_prices_company_id`      | `company_id`       | No     | No      |
| `idx_stock_prices_date`            | `date`             | No     | No      |

### Keys & Constraints

| Type        | Column               | Rule                                                              |
|-------------|----------------------|-------------------------------------------------------------------|
| Primary key | `id`                 | `SERIAL` surrogate key                                            |
| Foreign key | `company_id`         | `REFERENCES companies(id)`                                        |
| Unique      | `(company_id, date)` | One price record per ticker per day; `ON CONFLICT DO NOTHING` target |
| NOT NULL    | `company_id`, `date` | Both required on every row                                        |

---

## 4. `correlations`

Stores the **latest** pairwise Pearson correlations by period. Upserted on every ETL run — always reflects the most recent window. Now covers 6 tickers → 15 pairs × 2 periods = 30 rows.

### Data — all 30 rows (as of 2026-06-03)

| symbol_1 | symbol_2 | period | corr_value |
|----------|----------|--------|------------|
| ARM      | AVGO     | 1m     |  0.3017    |
| ARM      | GOOGL    | 1m     |  0.2529    |
| ARM      | MSFT     | 1m     |  0.1889    |
| ARM      | NVDA     | 1m     |  0.4143    |
| ARM      | TSM      | 1m     |  0.5203    |
| AVGO     | GOOGL    | 1m     | -0.1305    |
| AVGO     | MSFT     | 1m     |  0.0201    |
| AVGO     | NVDA     | 1m     |  0.2886    |
| AVGO     | TSM      | 1m     |  0.3916    |
| GOOGL    | MSFT     | 1m     |  0.0299    |
| GOOGL    | NVDA     | 1m     |  0.3003    |
| GOOGL    | TSM      | 1m     |  0.2400    |
| MSFT     | NVDA     | 1m     |  0.0352    |
| MSFT     | TSM      | 1m     | -0.1138    |
| NVDA     | TSM      | 1m     |  **0.6708**|
| ARM      | AVGO     | 6m     |  0.3566    |
| ARM      | GOOGL    | 6m     |  0.2446    |
| ARM      | MSFT     | 6m     |  0.1512    |
| ARM      | NVDA     | 6m     |  0.3860    |
| ARM      | TSM      | 6m     |  0.4924    |
| AVGO     | GOOGL    | 6m     |  0.3220    |
| AVGO     | MSFT     | 6m     |  0.2675    |
| AVGO     | NVDA     | 6m     |  0.5161    |
| AVGO     | TSM      | 6m     |  0.5637    |
| GOOGL    | MSFT     | 6m     |  0.0489    |
| GOOGL    | NVDA     | 6m     |  0.2437    |
| GOOGL    | TSM      | 6m     |  0.3830    |
| MSFT     | NVDA     | 6m     |  0.3109    |
| MSFT     | TSM      | 6m     |  0.1194    |
| NVDA     | TSM      | 6m     |  **0.6301**|

> Strongest pair: **NVDA/TSM** across both windows. Weakest short-term: **AVGO/GOOGL (r = -0.13)** and **MSFT/TSM (r = -0.11)** — the only negative correlations. `calculated_at` refreshes to `NOW()` on every ETL upsert.

### Column Metadata

| Column          | Data Type                     | Precision / Scale | Nullable | Default                          |
|-----------------|-------------------------------|-------------------|----------|----------------------------------|
| `id`            | `integer`                     | 32-bit            | NO       | `nextval('correlations_id_seq')` |
| `company_id_1`  | `integer`                     | 32-bit            | NO       | —                                |
| `company_id_2`  | `integer`                     | 32-bit            | NO       | —                                |
| `period`        | `character varying`           | max 10 chars      | NO       | —                                |
| `corr_value`    | `numeric`                     | 6 digits, 4 dec   | YES      | —                                |
| `calculated_at` | `timestamp without time zone` | —                 | YES      | `now()`                          |

### Keys & Constraints

| Type        | Column / Expression                    | Rule                                                          |
|-------------|----------------------------------------|---------------------------------------------------------------|
| Primary key | `id`                                   | `SERIAL` surrogate key                                        |
| Foreign key | `company_id_1`                         | `REFERENCES companies(id)`                                    |
| Foreign key | `company_id_2`                         | `REFERENCES companies(id)`                                    |
| Unique      | `(company_id_1, company_id_2, period)` | One value per ordered pair per period; `ON CONFLICT DO UPDATE` target |
| CHECK       | `period IN ('1m', '6m')`               | Rejects unsupported windows at the DB level                   |
| CHECK       | `corr_value BETWEEN -1 AND 1`          | Enforces valid Pearson r range                                |

### Normalization
Pairs are stored in one direction only (`symbol_1 < symbol_2` alphabetically, enforced at ETL) to avoid symmetric duplicates. The dashboard reconstructs the full symmetric matrix at render time.

---

## 5. `correlation_history`

Timestamped archive of correlation snapshots. One row per pair / period / day — populated by every ETL run and by `etl/backfill_history.py`. Enables the commentary agent to compare current values against a baseline ≥ 30 days ago.

### Sample Data

| snapshot_date | symbol_1 | symbol_2 | period | corr_value |
|---------------|----------|----------|--------|------------|
| 2025-12-05    | ARM      | NVDA     | 1m     |  0.4821    |
| 2025-12-05    | NVDA     | TSM      | 1m     |  0.7102    |
| 2026-06-03    | ARM      | NVDA     | 1m     |  0.4143    |
| 2026-06-03    | NVDA     | TSM      | 1m     |  0.6708    |

> **550 rows total.** 27 weekly snapshots (2025-12-05 → 2026-06-03) × ~20 pairs per snapshot. The UNIQUE constraint on `(company_id_1, company_id_2, period, snapshot_date)` makes the ETL idempotent — re-running on the same day inserts nothing.

### Column Metadata

| Column          | Data Type   | Precision / Scale | Nullable | Default                                  |
|-----------------|-------------|-------------------|----------|------------------------------------------|
| `id`            | `integer`   | 32-bit            | NO       | `nextval('correlation_history_id_seq')`  |
| `company_id_1`  | `integer`   | 32-bit            | NO       | —                                        |
| `company_id_2`  | `integer`   | 32-bit            | NO       | —                                        |
| `period`        | `varchar`   | max 10 chars      | NO       | —                                        |
| `corr_value`    | `numeric`   | 6 digits, 4 dec   | YES      | —                                        |
| `snapshot_date` | `date`      | —                 | NO       | `CURRENT_DATE`                           |

### Indexes

| Index name                  | Columns                                                   | Unique | Primary |
|-----------------------------|-----------------------------------------------------------|--------|---------|
| `correlation_history_pkey`  | `id`                                                      | Yes    | Yes     |
| `correlation_history_…_key` | `company_id_1, company_id_2, period, snapshot_date`       | Yes    | No      |
| `idx_corr_history_lookup`   | `company_id_1, company_id_2, period, snapshot_date DESC`  | No     | No      |

### Keys & Constraints

| Type        | Column / Expression                                             | Rule                                          |
|-------------|-----------------------------------------------------------------|-----------------------------------------------|
| Primary key | `id`                                                            | `SERIAL` surrogate key                        |
| Foreign key | `company_id_1`                                                  | `REFERENCES companies(id)`                    |
| Foreign key | `company_id_2`                                                  | `REFERENCES companies(id)`                    |
| Unique      | `(company_id_1, company_id_2, period, snapshot_date)`           | One snapshot per pair per day; idempotent ETL |
| CHECK       | `period IN ('1m', '6m')`                                        | Mirrors `correlations` constraint             |
| CHECK       | `corr_value BETWEEN -1 AND 1`                                   | Valid Pearson r range                         |

### Relationships

| Related table | Type | Direction                                                          |
|---------------|----|----------------------------------------------------------------------|
| `companies`   | N:1 | Both `company_id_1` and `company_id_2` reference `companies.id`    |

---

## 6. `correlation_alerts`

Stores AI-generated plain-English commentary produced by `agent/commentary.py`. One row per `corr_date` on which commentary was generated. Standalone — no foreign keys.

### Data — all rows

| id | generated_at        | corr_date  | baseline_date | commentary (truncated)                                 |
|----|---------------------|------------|---------------|--------------------------------------------------------|
|  2 | 2026-06-03 13:48:56 | 2026-06-03 | 2026-05-01    | Over the past month, the most striking development...  |
|  3 | 2026-06-03 13:51:11 | 2026-06-03 | 2026-05-01    | **Key moves:** ARM/NVDA surged...                      |
|  4 | 2026-06-03 13:58:17 | 2026-06-03 | 2026-05-01    | **Key moves:** ARM/NVDA surged...                      |
|  6 | 2026-06-03 14:02:21 | 2026-06-01 | 2026-05-01    | **Key moves:** AVGO/TSM and AVGO/GOOGL saw...          |

> `corr_date` is the analysis reference date (driven by sidebar `end_date`). `baseline_date` is the snapshot ~30 days prior actually used. Multiple rows for the same `corr_date` can exist if regenerated; the latest `generated_at` is shown in the dashboard.

### Column Metadata

| Column          | Data Type                     | Nullable | Default                                 |
|-----------------|-------------------------------|----------|-----------------------------------------|
| `id`            | `integer`                     | NO       | `nextval('correlation_alerts_id_seq')`  |
| `generated_at`  | `timestamp without time zone` | YES      | `now()`                                 |
| `corr_date`     | `date`                        | NO       | —                                       |
| `baseline_date` | `date`                        | NO       | —                                       |
| `commentary`    | `text`                        | NO       | —                                       |

### Indexes

| Index name           | Columns        | Unique | Primary |
|----------------------|----------------|--------|---------|
| `correlation_alerts_pkey` | `id`      | Yes    | Yes     |
| `idx_corr_alerts_ts` | `generated_at DESC` | No | No     |

### Relationships
None. Standalone table — decoupled from companies so a failed agent call never affects the core data.

---

## 7. `etl_log`

Audit log for every pipeline run. Standalone — no foreign keys by design so a failed run's log entry is never rolled back with the failed transaction.

### Data — most recent 3 of 9 rows

| id | run_at              | status  | rows_inserted | rows_skipped | tickers                       | duration_sec |
|----|---------------------|---------|---------------|--------------|-------------------------------|--------------|
|  9 | 2026-06-03 13:58:21 | success | 30            | 6,956        | ARM,AVGO,GOOGL,NVDA,TSM,MSFT  | 12.36        |
|  8 | 2026-06-03 13:58:17 | success | 86            | 6,900        | ARM,AVGO,GOOGL,NVDA,TSM,MSFT  |  9.29        |
|  7 | 2026-06-03 13:40:00 | success | 20            | 5,701        | NVDA,GOOGL,AVGO,ARM,TSM       |  3.45        |

> `rows_inserted` counts both stock_prices and correlation upserts combined. `rows_skipped` reflects the idempotent ON CONFLICT DO NOTHING behaviour — high skip counts are normal on re-runs.

### Column Metadata

| Column          | Data Type                     | Precision / Scale | Nullable | Default                     |
|-----------------|-------------------------------|-------------------|----------|-----------------------------|
| `id`            | `integer`                     | 32-bit            | NO       | `nextval('etl_log_id_seq')` |
| `run_at`        | `timestamp without time zone` | —                 | YES      | `now()`                     |
| `status`        | `character varying`           | max 20 chars      | NO       | —                           |
| `rows_inserted` | `integer`                     | 32-bit            | YES      | `0`                         |
| `rows_skipped`  | `integer`                     | 32-bit            | YES      | `0`                         |
| `tickers`       | `text`                        | unlimited         | YES      | —                           |
| `duration_sec`  | `numeric`                     | 8 digits, 2 dec   | YES      | —                           |
| `error_msg`     | `text`                        | unlimited         | YES      | —                           |

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
