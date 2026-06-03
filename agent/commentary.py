"""
Correlation Regime Alerts & Commentary agent.

Compares today's correlation snapshot against the one ~30 calendar days ago
and generates a plain-English summary via the Claude API.

Skips silently when:
  - ANTHROPIC_API_KEY is not set
  - No baseline snapshot ≥ 30 days old exists yet
"""
import os
from datetime import date, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BASELINE_DAYS = 30  # calendar days between current and baseline snapshot


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _fetch_snapshot(cur, as_of_date: date) -> list[tuple]:
    """Return the latest correlation_history snapshot on or before as_of_date."""
    cur.execute(
        """
        SELECT c1.symbol, c2.symbol, ch.period, ch.corr_value
        FROM correlation_history ch
        JOIN companies c1 ON ch.company_id_1 = c1.id
        JOIN companies c2 ON ch.company_id_2 = c2.id
        WHERE ch.snapshot_date = (
            SELECT MAX(snapshot_date)
            FROM correlation_history
            WHERE snapshot_date <= %s
        )
        ORDER BY ch.period, c1.symbol, c2.symbol
        """,
        (as_of_date,),
    )
    return cur.fetchall()


def generate_commentary(conn=None, as_of_date: date = None) -> dict | None:
    """
    Compare correlations at as_of_date against the snapshot ~30 days prior.
    Defaults to today when as_of_date is None.
    Inserts a row into correlation_alerts and returns the result dict.
    Returns None if there is insufficient history or no API key.

    If conn is provided it is used as-is (caller is responsible for commit/rollback).
    If conn is None a new connection is opened and closed here.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("  Skipping commentary — ANTHROPIC_API_KEY not set.")
        return None

    close_conn = conn is None
    if conn is None:
        conn = _get_connection()

    try:
        cur = conn.cursor()
        today = as_of_date or date.today()
        baseline_cutoff = today - timedelta(days=BASELINE_DAYS)

        cur.execute(
            "SELECT MAX(snapshot_date) FROM correlation_history WHERE snapshot_date <= %s",
            (baseline_cutoff,),
        )
        baseline_date = cur.fetchone()[0]

        if baseline_date is None:
            print(
                f"  Skipping commentary — no snapshot older than {BASELINE_DAYS} days yet "
                f"(need data from before {baseline_cutoff})."
            )
            return None

        current_rows = _fetch_snapshot(cur, today)
        baseline_rows = _fetch_snapshot(cur, baseline_date)

        if not current_rows or not baseline_rows:
            return None

        current_dict = {(r[0], r[1], r[2]): float(r[3]) for r in current_rows}
        baseline_dict = {(r[0], r[1], r[2]): float(r[3]) for r in baseline_rows}

        delta_lines = []
        for key in sorted(current_dict):
            sym1, sym2, period = key
            curr = current_dict[key]
            base = baseline_dict.get(key)
            if base is not None:
                delta_lines.append(
                    f"  {sym1}/{sym2} ({period}): {base:+.3f} → {curr:+.3f}  (Δ {curr - base:+.3f})"
                )

        if not delta_lines:
            return None

        from anthropic import Anthropic

        client = Anthropic()
        prompt = (
            "You are a quantitative analyst reviewing pairwise stock correlation shifts.\n\n"
            f"Comparison window: {baseline_date} → {today}\n"
            "Values are Pearson r of daily returns. "
            "'1m' = 21-trading-day window, '6m' = 126-trading-day window.\n\n"
            "Changes:\n"
            + "\n".join(delta_lines)
            + "\n\n"
            "Write a summary in UNDER 100 WORDS. Cover three things in order:\n"
            "1. Key correlation changes (largest |Δ| moves)\n"
            "2. Outliers (any pair behaving differently from the group)\n"
            "3. Overall trend (tightening, loosening, or mixed)\n"
            "Do not quote raw numbers. Be direct and factual. Stop before 100 words."
        )

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        commentary_text = response.content[0].text.strip()

        cur.execute(
            """INSERT INTO correlation_alerts (corr_date, baseline_date, commentary)
               VALUES (%s, %s, %s)""",
            (today, baseline_date, commentary_text),
        )

        if close_conn:
            conn.commit()

        return {
            "corr_date": str(today),
            "baseline_date": str(baseline_date),
            "commentary": commentary_text,
        }

    finally:
        if close_conn:
            conn.close()
