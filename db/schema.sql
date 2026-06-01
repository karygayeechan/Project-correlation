CREATE TABLE IF NOT EXISTS companies (
    id      SERIAL PRIMARY KEY,
    symbol  VARCHAR(10) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS company_details (
    id           INT PRIMARY KEY REFERENCES companies(id),
    company_name VARCHAR(255),
    sector       VARCHAR(100),
    industry     VARCHAR(100),
    market_cap   BIGINT
);

CREATE TABLE IF NOT EXISTS stock_prices (
    id         SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    date       DATE NOT NULL,
    open       NUMERIC(12, 4),
    high       NUMERIC(12, 4),
    low        NUMERIC(12, 4),
    close      NUMERIC(12, 4),
    adj_close  NUMERIC(12, 4),
    volume     BIGINT,
    UNIQUE (company_id, date)
);

CREATE INDEX IF NOT EXISTS idx_stock_prices_company_id ON stock_prices(company_id);
CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON stock_prices(date);

CREATE TABLE IF NOT EXISTS correlations (
    id            SERIAL PRIMARY KEY,
    company_id_1  INT NOT NULL REFERENCES companies(id),
    company_id_2  INT NOT NULL REFERENCES companies(id),
    period        VARCHAR(10) NOT NULL CHECK (period IN ('1m', '6m')),
    corr_value    NUMERIC(6, 4) CHECK (corr_value BETWEEN -1 AND 1),
    calculated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (company_id_1, company_id_2, period)
);

CREATE INDEX IF NOT EXISTS idx_correlations_company_id_1 ON correlations(company_id_1);
CREATE INDEX IF NOT EXISTS idx_correlations_company_id_2 ON correlations(company_id_2);
CREATE INDEX IF NOT EXISTS idx_correlations_period ON correlations(period);

CREATE TABLE IF NOT EXISTS etl_log (
    id            SERIAL PRIMARY KEY,
    run_at        TIMESTAMP DEFAULT NOW(),
    status        VARCHAR(20) NOT NULL,
    rows_inserted INT DEFAULT 0,
    rows_skipped  INT DEFAULT 0,
    tickers       TEXT,
    duration_sec  NUMERIC(8, 2),
    error_msg     TEXT
);
