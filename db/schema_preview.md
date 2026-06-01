# Database Schema Preview

5 tables, 3 indexes per major table, fully normalized to 3NF.

---

## Entity Relationship Overview

```
companies (1) ──────────── (1) company_details
    │
    │  (1)
    │
    ├──────────────────────── (N) stock_prices
    │
    └── (1) company_id_1 ─┐
                           ├── correlations
        (1) company_id_2 ─┘

etl_log  (standalone — no FK relationships)
```

- `companies` → `company_details` : **1:1** — one detail record per company
- `companies` → `stock_prices` : **1:N** — one company has many daily price rows
- `companies` → `correlations` : **1:N (self-join)** — each row joins two companies; one company appears in many correlation pairs

---

## Table 1 — `companies`

The parent/root table. All other domain tables foreign-key back here.

| Column   | Type          | Constraints                  |
|----------|---------------|------------------------------|
| `id`     | `SERIAL`      | PRIMARY KEY                  |
| `symbol` | `VARCHAR(10)` | UNIQUE, NOT NULL             |

**Keys**
- Primary key: `id` (surrogate, auto-increment)
- Unique constraint: `symbol` — enforces one row per ticker

**Normalization note**
Ticker symbols are intentionally separated from `company_details`. Symbols can be reassigned (e.g. FB → META) while the company's integer `id` and all child rows remain stable.

---

## Table 2 — `company_details`

Extends `companies` with descriptive metadata. Modelled as a separate table rather than additional columns on `companies` to keep the parent table narrow and enforce a strict 1:1 boundary.

| Column         | Type           | Constraints                          |
|----------------|----------------|--------------------------------------|
| `id`           | `INT`          | PRIMARY KEY, FK → `companies(id)`    |
| `company_name` | `VARCHAR(255)` | —                                    |
| `sector`       | `VARCHAR(100)` | —                                    |
| `industry`     | `VARCHAR(100)` | —                                    |
| `market_cap`   | `BIGINT`       | —                                    |

**Keys**
- Primary key: `id` — same value as `companies.id` (shared PK pattern, not a separate surrogate)
- Foreign key: `id` → `companies(id)`

**Relationship: 1:1 with `companies`**
The shared primary key (`id` is both PK and FK) guarantees exactly one `company_details` row per company — the DB structurally prevents duplicates.

**Normalization note**
Descriptive attributes (`sector`, `industry`, `market_cap`) are non-key facts about the company entity, not about the ticker symbol or price series. Placing them in a separate table is a 2NF / decomposition choice: the `companies` table stays focused on identity, `company_details` on descriptive metadata that changes infrequently.

---

## Table 3 — `stock_prices`

Time-series table. One row per company per trading day.

| Column       | Type            | Constraints                               |
|--------------|-----------------|-------------------------------------------|
| `id`         | `SERIAL`        | PRIMARY KEY                               |
| `company_id` | `INT`           | NOT NULL, FK → `companies(id)`            |
| `date`       | `DATE`          | NOT NULL                                  |
| `open`       | `NUMERIC(12,4)` | —                                         |
| `high`       | `NUMERIC(12,4)` | —                                         |
| `low`        | `NUMERIC(12,4)` | —                                         |
| `close`      | `NUMERIC(12,4)` | —                                         |
| `adj_close`  | `NUMERIC(12,4)` | —                                         |
| `volume`     | `BIGINT`        | —                                         |

**Keys**
- Primary key: `id` (surrogate)
- Foreign key: `company_id` → `companies(id)`
- Composite unique constraint: `(company_id, date)` — one price record per ticker per day; also used as the ON CONFLICT target for idempotent inserts

**Indexes**

| Index name                      | Column(s)    | Purpose                                                         |
|---------------------------------|--------------|-----------------------------------------------------------------|
| `idx_stock_prices_company_id`   | `company_id` | Fast lookup of all rows for a given ticker                      |
| `idx_stock_prices_date`         | `date`       | Fast date-range scans used by the dashboard's chart queries     |

**Relationship: 1:N with `companies`**
One company has many price rows (one per trading day). The composite unique constraint means the relationship is also a natural compound key.

**Normalization note**
`adj_close` is stored alongside `close` rather than derived on-the-fly. Adjusted close accounts for splits and dividends; raw close reflects the actual traded price. Keeping both avoids re-computation and ensures the return calculations used in correlation analysis are reproducible without re-fetching from yfinance.

---

## Table 4 — `correlations`

Stores precomputed pairwise Pearson correlations between companies, keyed by period. This is a **self-join** on `companies` — both `company_id_1` and `company_id_2` reference the same parent table.

| Column         | Type            | Constraints                                                      |
|----------------|-----------------|------------------------------------------------------------------|
| `id`           | `SERIAL`        | PRIMARY KEY                                                      |
| `company_id_1` | `INT`           | NOT NULL, FK → `companies(id)`                                   |
| `company_id_2` | `INT`           | NOT NULL, FK → `companies(id)`                                   |
| `period`       | `VARCHAR(10)`   | NOT NULL, CHECK (`period IN ('1m', '6m')`)                       |
| `corr_value`   | `NUMERIC(6,4)`  | CHECK (`corr_value BETWEEN -1 AND 1`)                            |
| `calculated_at`| `TIMESTAMP`     | DEFAULT `NOW()`                                                  |

**Keys**
- Primary key: `id` (surrogate)
- Foreign key: `company_id_1` → `companies(id)`
- Foreign key: `company_id_2` → `companies(id)`
- Composite unique constraint: `(company_id_1, company_id_2, period)` — one correlation value per ordered pair per period; used as the ON CONFLICT target for upserts

**Constraints**
- `CHECK (period IN ('1m', '6m'))` — restricts to the two supported windows (1 month ≈ 21 trading days, 6 months ≈ 126 trading days)
- `CHECK (corr_value BETWEEN -1 AND 1)` — enforces valid Pearson r range at the DB level

**Indexes**

| Index name                        | Column(s)      | Purpose                                                          |
|-----------------------------------|----------------|------------------------------------------------------------------|
| `idx_correlations_company_id_1`   | `company_id_1` | Fast lookup of all pairs where a company is the first member     |
| `idx_correlations_company_id_2`   | `company_id_2` | Fast lookup of all pairs where a company is the second member    |
| `idx_correlations_period`         | `period`       | Fast filtering by period (e.g. all 1m correlations for heatmap) |

**Relationship: self-join 1:N on `companies`**
Each `companies` row can appear as `company_id_1` or `company_id_2` in many correlation rows. Pairs are stored in one direction only (`company_id_1 < company_id_2` enforced at the ETL layer) to avoid symmetric duplicates — the dashboard reflects both directions when rendering the heatmap.

**Normalization note**
Correlations are precomputed and stored rather than derived at query time. With N tickers there are N(N−1)/2 unique pairs × 2 periods = 20 rows for 5 tickers. This keeps dashboard reads O(1) for the stored snapshot. The dashboard also supports on-the-fly recomputation from `stock_prices` for arbitrary date windows.

---

## Table 5 — `etl_log`

Audit log for every pipeline run. No foreign keys — fully standalone.

| Column          | Type            | Constraints          |
|-----------------|-----------------|----------------------|
| `id`            | `SERIAL`        | PRIMARY KEY          |
| `run_at`        | `TIMESTAMP`     | DEFAULT `NOW()`      |
| `status`        | `VARCHAR(20)`   | NOT NULL             |
| `rows_inserted` | `INT`           | DEFAULT `0`          |
| `rows_skipped`  | `INT`           | DEFAULT `0`          |
| `tickers`       | `TEXT`          | —                    |
| `duration_sec`  | `NUMERIC(8,2)`  | —                    |
| `error_msg`     | `TEXT`          | —                    |

**Keys**
- Primary key: `id` (surrogate)

**No foreign keys by design**
`etl_log` records pipeline runs independently of the data they produced. A failed run (status = `error`) still writes a row even if no company or price rows were committed — decoupling the log from transactional data ensures the audit trail is never rolled back with a failed ETL transaction.

**Normalization note**
`tickers` is stored as a comma-separated `TEXT` field rather than a normalized junction table. The log is append-only and read for display only — the extra join overhead of a normalized structure has no benefit here and the denormalized field keeps each log row self-contained for human review.

---

## Summary

| Table             | PK type   | FK(s)                                | Unique constraint(s)                        | Check constraint(s)               | Indexes |
|-------------------|-----------|--------------------------------------|---------------------------------------------|-----------------------------------|---------|
| `companies`       | Surrogate | —                                    | `symbol`                                    | —                                 | —       |
| `company_details` | Shared PK | `id → companies`                     | `id` (via PK)                               | —                                 | —       |
| `stock_prices`    | Surrogate | `company_id → companies`             | `(company_id, date)`                        | —                                 | 2       |
| `correlations`    | Surrogate | `company_id_1 → companies`, `company_id_2 → companies` | `(company_id_1, company_id_2, period)` | `period IN (...)`, `corr_value BETWEEN -1 AND 1` | 3 |
| `etl_log`         | Surrogate | —                                    | —                                           | —                                 | —       |
