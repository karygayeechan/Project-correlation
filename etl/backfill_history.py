"""
One-time backfill of correlation_history from existing stock_prices data.

Computes what the 1m and 6m correlations actually were on each past snapshot date
by calling the same on-the-fly calculation the dashboard uses, then stores each
result in correlation_history so the commentary agent has a baseline to compare against.

Usage:
    python etl/backfill_history.py                   # 6 months back, weekly snapshots
    python etl/backfill_history.py --months 12        # 12 months back
    python etl/backfill_history.py --interval 1       # daily snapshots
"""
import argparse
import os
import sys
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from app.db import get_corr_heatmap, get_tickers


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def backfill(months_back: int = 6, interval_days: int = 7):
    tickers = get_tickers()
    if not tickers:
        print("No tickers in DB. Run ETL first.")
        return

    conn = _get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, symbol FROM companies WHERE symbol = ANY(%s)", (tickers,))
    company_ids = {sym: cid for cid, sym in cur.fetchall()}

    today = date.today()
    start = today - timedelta(days=months_back * 30)

    snap_dates = []
    d = start
    while d < today:
        snap_dates.append(d)
        d += timedelta(days=interval_days)
    snap_dates.append(today)

    sorted_tickers = sorted(tickers)
    print(f"Backfilling {len(snap_dates)} snapshots from {snap_dates[0]} to {snap_dates[-1]}...")
    print(f"Tickers: {sorted_tickers}\n")

    total_inserted = 0
    for snap_date in snap_dates:
        rows = []
        for period in ("1m", "6m"):
            mat = get_corr_heatmap(sorted_tickers, period, snap_date)
            if mat.empty:
                continue
            for i, sym1 in enumerate(sorted_tickers):
                for sym2 in sorted_tickers[i + 1:]:
                    if sym1 not in mat.index or sym2 not in mat.columns:
                        continue
                    val = mat.loc[sym1, sym2]
                    if val is None or str(val) == "nan":
                        continue
                    cid1 = company_ids.get(sym1)
                    cid2 = company_ids.get(sym2)
                    if cid1 and cid2:
                        rows.append((cid1, cid2, period, float(val), snap_date))

        if rows:
            execute_values(
                cur,
                """INSERT INTO correlation_history
                   (company_id_1, company_id_2, period, corr_value, snapshot_date)
                   VALUES %s
                   ON CONFLICT (company_id_1, company_id_2, period, snapshot_date) DO NOTHING""",
                rows,
            )
            inserted = cur.rowcount
            total_inserted += inserted
            print(f"  {snap_date}: {inserted} rows inserted")
        else:
            print(f"  {snap_date}: no data (likely before price history begins)")

    conn.commit()
    conn.close()
    print(f"\nBackfill complete — {total_inserted} rows inserted into correlation_history.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6, help="How far back to backfill (default: 6)")
    parser.add_argument("--interval", type=int, default=7, help="Days between snapshots (default: 7)")
    args = parser.parse_args()
    backfill(months_back=args.months, interval_days=args.interval)
