import os
import sys
import time
from datetime import date

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                          # etl/ — for extract, transform
sys.path.insert(0, os.path.join(_HERE, ".."))      # project root — for agent/

from extract import fetch_raw, fetch_ticker_info, TICKERS
from transform import reshape, compute_correlations
from agent.commentary import generate_commentary


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_active_tickers_from_db(cur) -> list[str]:
    cur.execute("SELECT symbol FROM companies ORDER BY symbol")
    return [row[0] for row in cur.fetchall()]


def insert_companies(cur, ticker_info: list[dict]) -> dict[str, int]:
    for record in ticker_info:
        cur.execute(
            "INSERT INTO companies (symbol) VALUES (%s) ON CONFLICT DO NOTHING",
            (record["symbol"],),
        )
    symbols = [r["symbol"] for r in ticker_info]
    cur.execute(
        "SELECT id, symbol FROM companies WHERE symbol = ANY(%s)",
        (symbols,),
    )
    return {symbol: company_id for company_id, symbol in cur.fetchall()}


def insert_company_details(cur, ticker_info: list[dict], company_ids: dict[str, int]):
    for record in ticker_info:
        company_id = company_ids.get(record["symbol"])
        if company_id is None:
            continue
        cur.execute(
            """INSERT INTO company_details (id, company_name, sector, industry, market_cap)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (
                company_id,
                record.get("company_name"),
                record.get("sector"),
                record.get("industry"),
                record.get("market_cap"),
            ),
        )


def insert_stock_prices(cur, long_df: pd.DataFrame, company_ids: dict[str, int]) -> tuple[int, int]:
    rows = []
    for row in long_df.itertuples():
        cid = company_ids.get(row.symbol)
        if cid is None:
            continue
        rows.append((
            cid,
            row.date,
            None if pd.isna(row.open) else float(row.open),
            None if pd.isna(row.high) else float(row.high),
            None if pd.isna(row.low) else float(row.low),
            None if pd.isna(row.close) else float(row.close),
            None if pd.isna(row.adj_close) else float(row.adj_close),
            None if pd.isna(row.volume) else int(row.volume),
        ))

    if not rows:
        return 0, 0

    execute_values(
        cur,
        """INSERT INTO stock_prices (company_id, date, open, high, low, close, adj_close, volume)
           VALUES %s ON CONFLICT (company_id, date) DO NOTHING""",
        rows,
    )
    inserted = cur.rowcount
    skipped = len(rows) - inserted
    return inserted, skipped


def upsert_correlations(cur, corr_df: pd.DataFrame, company_ids: dict[str, int]) -> int:
    """Insert or update correlation values. Always reflects the latest computed data."""
    rows = []
    for row in corr_df.itertuples():
        cid1 = company_ids.get(row.symbol_1)
        cid2 = company_ids.get(row.symbol_2)
        if cid1 is None or cid2 is None:
            continue
        rows.append((cid1, cid2, row.period, float(row.corr_value)))

    if not rows:
        return 0

    execute_values(
        cur,
        """INSERT INTO correlations (company_id_1, company_id_2, period, corr_value)
           VALUES %s
           ON CONFLICT (company_id_1, company_id_2, period)
           DO UPDATE SET corr_value = EXCLUDED.corr_value, calculated_at = NOW()""",
        rows,
    )
    return len(rows)


def archive_correlation_snapshot(cur, corr_df: pd.DataFrame, company_ids: dict[str, int]):
    """Copy today's computed correlations into correlation_history.
    Idempotent — the UNIQUE constraint silently skips duplicate runs on the same day."""
    today = date.today()
    rows = []
    for row in corr_df.itertuples():
        cid1 = company_ids.get(row.symbol_1)
        cid2 = company_ids.get(row.symbol_2)
        if cid1 is None or cid2 is None:
            continue
        rows.append((cid1, cid2, row.period, float(row.corr_value), today))

    if rows:
        execute_values(
            cur,
            """INSERT INTO correlation_history
               (company_id_1, company_id_2, period, corr_value, snapshot_date)
               VALUES %s ON CONFLICT (company_id_1, company_id_2, period, snapshot_date) DO NOTHING""",
            rows,
        )


def log_run(cur, status: str, rows_inserted: int, rows_skipped: int, tickers: list[str], duration_sec: float, error_msg: str = None):
    cur.execute(
        """INSERT INTO etl_log (status, rows_inserted, rows_skipped, tickers, duration_sec, error_msg)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (status, rows_inserted, rows_skipped, ",".join(tickers), round(duration_sec, 2), error_msg),
    )


def remove_ticker_from_db(symbol: str):
    """Delete all data for a ticker — correlations, prices, details, company row."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM companies WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        if row is None:
            return
        cid = row[0]
        cur.execute("DELETE FROM correlation_history WHERE company_id_1 = %s OR company_id_2 = %s", (cid, cid))
        cur.execute("DELETE FROM correlations WHERE company_id_1 = %s OR company_id_2 = %s", (cid, cid))
        cur.execute("DELETE FROM stock_prices WHERE company_id = %s", (cid,))
        cur.execute("DELETE FROM company_details WHERE id = %s", (cid,))
        cur.execute("DELETE FROM companies WHERE id = %s", (cid,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run(tickers=None):
    """Run the full ETL pipeline.

    tickers: symbols to process. Pass None for the default set (TICKERS constant).
             Pass current_db_tickers + [new_symbol] when adding a ticker.
             Pass current_db_tickers when refreshing existing data.
    """
    if tickers is None:
        tickers = list(TICKERS)

    start = time.time()
    conn = None
    cur = None

    try:
        print(f"Fetching raw data for {tickers}...")
        raw_df = fetch_raw(tickers)

        print("Fetching ticker metadata...")
        ticker_info = fetch_ticker_info(tickers)

        print("Reshaping data...")
        long_df = reshape(raw_df, tickers)

        print("Computing correlations...")
        corr_df = compute_correlations(long_df)

        print("Connecting to database...")
        conn = get_connection()
        cur = conn.cursor()

        print("Inserting companies...")
        company_ids = insert_companies(cur, ticker_info)

        print("Inserting company details...")
        insert_company_details(cur, ticker_info, company_ids)

        print("Inserting stock prices...")
        sp_inserted, sp_skipped = insert_stock_prices(cur, long_df, company_ids)
        print(f"  stock_prices: {sp_inserted} inserted, {sp_skipped} skipped")

        print("Upserting correlations...")
        corr_count = upsert_correlations(cur, corr_df, company_ids)
        print(f"  correlations: {corr_count} upserted")

        conn.commit()

        print("Archiving correlation snapshot...")
        archive_correlation_snapshot(cur, corr_df, company_ids)
        conn.commit()

        print("Generating regime commentary...")
        try:
            commentary_result = generate_commentary(conn)
            if commentary_result:
                conn.commit()
                snippet = commentary_result["commentary"][:80].replace("\n", " ")
                print(f"  Commentary stored: {snippet}...")
            else:
                print("  Commentary skipped.")
        except Exception as commentary_err:
            print(f"  Commentary failed (non-fatal): {commentary_err}")
            conn.rollback()

        duration = time.time() - start
        log_run(cur, "success", sp_inserted + corr_count, sp_skipped, tickers, duration)
        conn.commit()

        print(f"\nETL complete in {duration:.2f}s")
        print(f"  Prices inserted: {sp_inserted}, skipped: {sp_skipped}")
        print(f"  Correlations upserted: {corr_count}")

    except Exception as e:
        duration = time.time() - start
        if conn:
            conn.rollback()
        if cur and conn:
            try:
                log_run(cur, "error", 0, 0, tickers, duration, str(e))
                conn.commit()
            except Exception:
                pass
        print(f"ETL failed: {e}")
        raise

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
