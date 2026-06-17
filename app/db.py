import os
from datetime import date, timedelta

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_latest_price_date():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(date) FROM stock_prices")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_tickers() -> list[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT symbol FROM companies ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_stock_prices(tickers: list[str], start_date, end_date) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    conn = get_connection()
    try:
        df = pd.read_sql(
            """
            SELECT sp.date, c.symbol, sp.open, sp.high, sp.low,
                   sp.close, sp.adj_close, sp.volume
            FROM stock_prices sp
            JOIN companies c ON sp.company_id = c.id
            WHERE c.symbol = ANY(%s)
              AND sp.date BETWEEN %s AND %s
            ORDER BY c.symbol, sp.date
            """,
            conn,
            params=(tickers, start_date, end_date),
        )
        df["date"] = pd.to_datetime(df["date"])
        return df
    finally:
        conn.close()


def get_corr_heatmap(tickers: list[str], period: str, end_date) -> pd.DataFrame:
    """Compute correlation matrix from stock_prices up to end_date.

    Returns a symmetric DataFrame (tickers × tickers) ready for heatmap rendering.
    """
    if not tickers or len(tickers) < 2:
        return pd.DataFrame()

    n_days = {"6m": 126, "12m": 252, "24m": 504, "60m": 1260}[period]
    lookback = end_date - timedelta(days=n_days * 3)

    prices = get_stock_prices(tickers, lookback, end_date)
    if prices.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values("date")
    prices_sorted = prices_sorted.copy()
    prices_sorted["daily_return"] = prices_sorted.groupby("symbol")["adj_close"].pct_change()

    pivot = prices_sorted.pivot(index="date", columns="symbol", values="daily_return")
    pivot.columns.name = None

    available = [t for t in tickers if t in pivot.columns]
    if len(available) < 2:
        return pd.DataFrame()

    corr = pivot[available].tail(n_days).corr(min_periods=10)
    return corr.loc[available, available]


def get_rolling_corr(sym1: str, sym2: str, start_date, end_date, window: int = 21) -> pd.Series:
    """Rolling Pearson correlation between two tickers over the date range."""
    prices = get_stock_prices([sym1, sym2], start_date, end_date)
    if prices.empty:
        return pd.Series(dtype=float, name="corr")

    prices_sorted = prices.sort_values("date").copy()
    prices_sorted["daily_return"] = prices_sorted.groupby("symbol")["adj_close"].pct_change()
    pivot = prices_sorted.pivot(index="date", columns="symbol", values="daily_return")
    pivot.columns.name = None

    if sym1 not in pivot.columns or sym2 not in pivot.columns:
        return pd.Series(dtype=float, name="corr")

    rolling = pivot[sym1].rolling(window, min_periods=max(5, window // 2)).corr(pivot[sym2])
    rolling.name = "corr"
    return rolling


def get_alert_for_date(corr_date) -> dict | None:
    """Return the stored alert for a specific corr_date, or None if not found."""
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT * FROM correlation_alerts WHERE corr_date = %s ORDER BY generated_at DESC LIMIT 1",
            conn,
            params=(corr_date,),
        )
        if df.empty:
            return None
        row = df.iloc[0]
        return {k: (None if str(v) == "nan" else v) for k, v in row.to_dict().items()}
    finally:
        conn.close()


def get_alerts(limit: int = 10) -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT * FROM correlation_alerts ORDER BY generated_at DESC LIMIT %s",
            conn,
            params=(limit,),
        )
        return df
    finally:
        conn.close()


def get_etl_log(limit: int = 50) -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT * FROM etl_log ORDER BY run_at DESC LIMIT %s",
            conn,
            params=(limit,),
        )
        return df
    finally:
        conn.close()
