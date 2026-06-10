"""
Regime commentary agent.

Fetches current macro regime indicators, evaluates all alert rules, then asks
Claude to write a ~100-word plain-English regime briefing. Stores the result
in the correlation_alerts table (same schema as before; baseline_date = corr_date
since there is no longer a baseline comparison).

Called on-demand from the API's GET /alerts/generate endpoint.
Skips silently when ANTHROPIC_API_KEY is not set.
"""
import os
from datetime import date

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def generate_commentary(conn=None, as_of_date: date = None) -> dict | None:
    """
    Fetch macro indicators, evaluate regime-alert rules, call Claude for a
    ~100-word briefing, and insert the result into correlation_alerts.

    Parameters
    ----------
    conn : existing psycopg2 connection (caller commits); None → opens/closes its own.
    as_of_date : date to stamp the record; defaults to today.

    Returns
    -------
    {"corr_date": str, "commentary": str} or None if skipped.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("  Skipping commentary — ANTHROPIC_API_KEY not set.")
        return None

    from data_collector import fetch_indicators
    from regime_alerts import detect_alerts
    from anthropic import Anthropic

    today = as_of_date or date.today()

    print("  Fetching macro indicators for regime commentary...")
    df = fetch_indicators(lookback_days=365)
    alerts = detect_alerts(df)

    latest = df.dropna(how="all").iloc[-1]
    triggered = [a for a in alerts if a["triggered"]]

    def _fmt(val, fmt):
        return fmt.format(val) if val is not None and not pd.isna(val) else "N/A"

    indicator_lines = [
        f"10Y Treasury yield: {_fmt(latest.get('treasury_10y'), '{:.3f}%')}",
        f"10Y TIPS real yield: {_fmt(latest.get('tips_10y'), '{:.2f}%')}",
        f"Nasdaq-100 breadth (% above 200DMA): {_fmt(latest.get('nasdaq_breadth'), '{:.1f}%')}",
        f"VIX: {_fmt(latest.get('vix'), '{:.1f}')}",
        f"SMH/QQQ ratio: {_fmt(latest.get('smh_qqq_ratio'), '{:.4f}')} "
        f"(z-score: {_fmt(latest.get('smh_qqq_zscore'), '{:.2f}')})",
    ]

    alert_lines = [
        f"- {a['indicator']}: {a['rule']} — {a['message']}"
        + (" [recent crossing]" if a["recently_crossed"] else "")
        for a in triggered
    ] or ["- None currently triggered"]

    prompt = (
        f"You are a macro market analyst writing a daily regime briefing. Today is {today}.\n\n"
        "Current macro readings:\n"
        + "\n".join(f"- {l}" for l in indicator_lines)
        + f"\n\nTriggered alerts ({len(triggered)} of {len(alerts)} rules):\n"
        + "\n".join(alert_lines)
        + "\n\n"
        "Write a ~100-word regime briefing in 2–3 sentences of flowing prose. "
        "Focus ONLY on the macro indicators above — do NOT mention individual stocks or pairs. "
        "Tell the reader what the current regime is, which signals deserve immediate attention, "
        "and what the net risk posture implies. Be direct and actionable."
    )

    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()

    close_conn = conn is None
    if conn is None:
        conn = _get_connection()

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO correlation_alerts (corr_date, baseline_date, commentary) VALUES (%s, %s, %s)",
            (today, today, text),
        )
        if close_conn:
            conn.commit()
        return {"corr_date": str(today), "commentary": text}
    finally:
        if close_conn:
            conn.close()
