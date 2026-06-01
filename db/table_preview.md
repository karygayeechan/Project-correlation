# Table Data Preview

Live snapshot of all 5 tables as of the most recent ETL run (2026-06-01).
Row counts: **5** companies · **5** company_details · **1,255** stock_prices · **20** correlations · **5** etl_log entries.

---

## `companies`

| id | symbol |
|----|--------|
| 2  | GOOGL  |
| 3  | AVGO   |
| 5  | TSM    |
| 6  | NVDA   |
| 21 | ARM    |

> IDs are non-sequential because rows were inserted across multiple ETL runs (AAPL was removed and replaced by NVDA; MSFT was briefly added and removed during testing). Surrogate keys are intentionally stable — gaps are normal.

---

## `company_details`

| id | company_name                                        | sector                 | industry                       | market_cap      |
|----|-----------------------------------------------------|------------------------|--------------------------------|-----------------|
| 2  | Alphabet Inc.                                       | Communication Services | Internet Content & Information | 4,550,075,875,328 |
| 3  | Broadcom Inc.                                       | Technology             | Semiconductors                 | 2,184,457,093,120 |
| 5  | Taiwan Semiconductor Manufacturing Company Limited | Technology             | Semiconductors                 | 2,317,576,044,544 |
| 6  | NVIDIA Corporation                                  | Technology             | Semiconductors                 | 5,362,526,715,904 |
| 21 | Arm Holdings plc                                    | Technology             | Semiconductors                 |   437,057,847,296 |

> `id` is a shared primary key — the same value as `companies.id`, enforcing the 1:1 relationship at the DB level.

---

## `stock_prices`

**1,255 rows total** — 251 trading days × 5 tickers (2025-06-02 → 2026-06-01).

**Earliest date (2025-06-02) — one row per ticker:**

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 252  | GOOGL  | 2025-06-02 | 167.8400 | 169.8700 | 167.3900 | 169.0300 | 168.4459  | 38,612,300  |
| 503  | AVGO   | 2025-06-02 | 243.2500 | 250.0000 | 243.1900 | 248.7100 | 246.7110  | 19,197,000  |
| 1005 | TSM    | 2025-06-02 | 193.0400 | 195.1600 | 192.2000 | 194.8400 | 192.4563  |  7,447,400  |
| 1256 | NVDA   | 2025-06-02 | 135.4900 | 138.1200 | 135.4000 | 137.3800 | 137.3476  | 197,663,100 |
| 5021 | ARM    | 2025-06-02 | 124.8000 | 127.4800 | 123.5880 | 126.0550 | 126.0550  |  3,297,500  |

**Latest date (2026-06-01) — one row per ticker:**

| id   | symbol | date       | open     | high     | low      | close    | adj_close | volume      |
|------|--------|------------|----------|----------|----------|----------|-----------|-------------|
| 502  | GOOGL  | 2026-06-01 | 376.6000 | 377.7050 | 373.5200 | 375.5600 | 375.5600  | 10,987,872  |
| 753  | AVGO   | 2026-06-01 | 450.1850 | 463.6200 | 442.2400 | 461.3350 | 461.3350  | 15,820,037  |
| 1255 | TSM    | 2026-06-01 | 424.8800 | 447.1500 | 422.6300 | 446.8500 | 446.8500  | 11,425,057  |
| 1506 | NVDA   | 2026-06-01 | 215.7800 | 222.4000 | 215.7000 | 221.3999 | 221.3999  | 124,254,884 |
| 5271 | ARM    | 2026-06-01 | 389.9500 | 421.6899 | 381.2500 | 409.2000 | 409.2000  | 16,486,393  |

> `adj_close` differs from `close` on earlier dates where splits or dividends occurred (e.g. GOOGL, AVGO, TSM). On the most recent date both values match because no adjustment has yet been applied. Returns and correlations are computed from `adj_close`.

---

## `correlations`

**20 rows** — 10 unique pairs × 2 periods (1m, 6m). Pairs stored with `symbol_1 < symbol_2` alphabetically to avoid symmetric duplicates.

| id | symbol_1 | symbol_2 | period | corr_value | calculated_at        |
|----|----------|----------|--------|------------|----------------------|
|  8 | AVGO     | GOOGL    | 1m     |  0.0370    | 2026-06-01 19:39:14  |
|  9 | AVGO     | TSM      | 1m     |  0.3885    | 2026-06-01 19:39:14  |
| 10 | GOOGL    | TSM      | 1m     |  0.3277    | 2026-06-01 19:39:14  |
| 26 | AVGO     | NVDA     | 1m     |  0.3567    | 2026-06-01 19:39:14  |
| 28 | GOOGL    | NVDA     | 1m     |  0.3090    | 2026-06-01 19:39:14  |
| 30 | NVDA     | TSM      | 1m     |  0.7316    | 2026-06-01 19:39:14  |
| 71 | ARM      | AVGO     | 1m     |  0.3872    | 2026-06-01 19:39:14  |
| 72 | ARM      | GOOGL    | 1m     |  0.2178    | 2026-06-01 19:39:14  |
| 73 | ARM      | NVDA     | 1m     |  0.3990    | 2026-06-01 19:39:14  |
| 74 | ARM      | TSM      | 1m     |  0.5859    | 2026-06-01 19:39:14  |
| 18 | AVGO     | GOOGL    | 6m     |  0.3603    | 2026-06-01 19:39:14  |
| 19 | AVGO     | TSM      | 6m     |  0.5616    | 2026-06-01 19:39:14  |
| 20 | GOOGL    | TSM      | 6m     |  0.3988    | 2026-06-01 19:39:14  |
| 36 | AVGO     | NVDA     | 6m     |  0.5255    | 2026-06-01 19:39:14  |
| 38 | GOOGL    | NVDA     | 6m     |  0.2472    | 2026-06-01 19:39:14  |
| 40 | NVDA     | TSM      | 6m     |  0.6410    | 2026-06-01 19:39:14  |
| 81 | ARM      | AVGO     | 6m     |  0.3720    | 2026-06-01 19:39:14  |
| 82 | ARM      | GOOGL    | 6m     |  0.2435    | 2026-06-01 19:39:14  |
| 83 | ARM      | NVDA     | 6m     |  0.3763    | 2026-06-01 19:39:14  |
| 84 | ARM      | TSM      | 6m     |  0.5134    | 2026-06-01 19:39:14  |

> IDs are non-sequential for the same reason as `companies` — rows were upserted across multiple runs. The strongest 1m pair is **NVDA / TSM (r = 0.73)**; the weakest is **AVGO / GOOGL (r = 0.04)**.

---

## `etl_log`

**5 rows** — full history of every pipeline run.

| id | run_at                      | status  | rows_inserted | rows_skipped | tickers                       | duration_sec | error_msg |
|----|-----------------------------|---------|---------------|--------------|-------------------------------|--------------|-----------|
| 1  | 2026-06-01 18:57:14         | success | 75            | 1200         | AAPL,GOOGL,AVGO,ARM,TSM       | 2.18         | —         |
| 2  | 2026-06-01 19:18:55         | success | 20            | 1255         | NVDA,GOOGL,AVGO,ARM,TSM       | 2.32         | —         |
| 3  | 2026-06-01 19:33:31         | success | 36            | 1500         | ARM,AVGO,GOOGL,NVDA,TSM,MSFT  | 2.77         | —         |
| 4  | 2026-06-01 19:38:44         | success | 75            | 1200         | AVGO,GOOGL,NVDA,TSM,ARM       | 1.77         | —         |
| 5  | 2026-06-01 19:39:14         | success | 20            | 1255         | ARM,AVGO,GOOGL,NVDA,TSM       | 1.59         | —         |

**Run history explained:**

| Run | What happened |
|-----|---------------|
| 1   | Initial load — AAPL was the fifth ticker; 75 new rows (55 prices + 20 correlations) |
| 2   | AAPL swapped for NVDA — AAPL deleted, NVDA inserted; 20 correlation upserts |
| 3   | MSFT briefly added via Manage Tickers tab (6-ticker run); later removed |
| 4   | Refresh after MSFT removal — 75 rows re-upserted for the final 5-ticker set |
| 5   | Final correlation upsert after ticker cleanup — 20 rows, all current |
