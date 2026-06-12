import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import adfuller

load_dotenv()


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def fetch_prices(sym_a: str, sym_b: str) -> tuple[pd.Series, pd.Series]:
    """Return latest 1-year adj_close Series for sym_a and sym_b, aligned by date."""
    end = date.today()
    start = end - timedelta(days=365)
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sp.date, c.symbol, sp.adj_close
            FROM stock_prices sp
            JOIN companies c ON sp.company_id = c.id
            WHERE c.symbol = ANY(%s)
              AND sp.date BETWEEN %s AND %s
            ORDER BY sp.date
            """,
            ([sym_a, sym_b], start, end),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=["date", "symbol", "adj_close"])
    df["date"] = pd.to_datetime(df["date"])
    df["adj_close"] = df["adj_close"].astype(float)
    pivot = df.pivot(index="date", columns="symbol", values="adj_close").dropna()
    return pivot[sym_a], pivot[sym_b]


def run_adf(series: pd.Series, label: str) -> dict:
    """Run ADF test on a price series. Returns stat, p-value, critical values, verdict."""
    result = adfuller(series.dropna(), autolag="AIC")
    stat, p_value, _, _, crit = result[0], result[1], result[2], result[3], result[4]
    is_stationary = p_value < 0.05
    # For raw prices: non-stationary (p>0.05) is expected → ✓; stationary → ?
    verdict = "?" if is_stationary else "✓"
    return {
        "label": label,
        "stat": stat,
        "p_value": p_value,
        "critical_values": crit,
        "is_stationary": is_stationary,
        "verdict": verdict,
    }


def run_engle_granger(series_a: pd.Series, series_b: pd.Series) -> dict:
    """
    Manual Engle-Granger procedure:
      1) OLS: regress A on B → get beta (hedge ratio)
      2) Residuals = A - beta * B
      3) ADF on residuals
    """
    x = add_constant(series_b.values.astype(float))
    model = OLS(series_a.values.astype(float), x).fit()
    alpha = float(model.params[0])
    beta = float(model.params[1])
    # ϵt = At − (α + β·Bt) — OLS residuals already include the intercept
    residuals = pd.Series(model.resid, index=series_a.index, name="spread")

    result = adfuller(residuals.dropna(), autolag="AIC")
    stat, p_value, crit = result[0], result[1], result[4]
    is_cointegrated = p_value < 0.05
    verdict = "✓" if is_cointegrated else "✗"
    return {
        "alpha": alpha,
        "beta": beta,
        "residuals": residuals,
        "stat": stat,
        "p_value": p_value,
        "critical_values": crit,
        "is_cointegrated": is_cointegrated,
        "verdict": verdict,
    }


def run_all(sym_a: str, sym_b: str) -> dict:
    """Run cointegration analysis on the latest 1 year of data, split into 4 quarters.

    The past year is divided into 4 equal quarterly windows (oldest = Q1, most recent = Q4).
    Each quarter produces its own EG p-value (both directions). The headline p-value per
    quarter is the primary direction (lower of the two). Pass condition per quarter: p < 0.05.

    Also runs ADF on the full-year series (prerequisite check) and full-year EG for the
    spread charts shown in the detailed sections.
    """
    series_a, series_b = fetch_prices(sym_a, sym_b)

    # Full-year ADF (prerequisite: both series should be non-stationary)
    adf_a = run_adf(series_a, sym_a)
    adf_b = run_adf(series_b, sym_b)

    # Full-year EG for spread charts in the detail section
    eg_ab_full = run_engle_granger(series_a, series_b)
    eg_ba_full = run_engle_granger(series_b, series_a)
    if eg_ab_full["p_value"] <= eg_ba_full["p_value"]:
        eg_primary, eg_rev = eg_ab_full, eg_ba_full
        eg_direction = f"{sym_a}→{sym_b}"
        eg_rev_direction = f"{sym_b}→{sym_a}"
    else:
        eg_primary, eg_rev = eg_ba_full, eg_ab_full
        eg_direction = f"{sym_b}→{sym_a}"
        eg_rev_direction = f"{sym_a}→{sym_b}"

    # Split into 4 quarterly windows and run EG on each
    n = len(series_a)
    q_size = n // 4
    quarters = []
    for q in range(4):
        start_idx = q * q_size
        end_idx = n if q == 3 else (q + 1) * q_size
        a_q = series_a.iloc[start_idx:end_idx]
        b_q = series_b.iloc[start_idx:end_idx]

        eg_ab_q = run_engle_granger(a_q, b_q)
        eg_ba_q = run_engle_granger(b_q, a_q)

        if eg_ab_q["p_value"] <= eg_ba_q["p_value"]:
            primary_p = eg_ab_q["p_value"]
            primary_direction = f"{sym_a}→{sym_b}"
        else:
            primary_p = eg_ba_q["p_value"]
            primary_direction = f"{sym_b}→{sym_a}"

        quarters.append({
            "label": f"Q{q + 1}",
            "start_date": a_q.index[0],
            "end_date": a_q.index[-1],
            "n_obs": len(a_q),
            "eg_ab": eg_ab_q,
            "eg_ba": eg_ba_q,
            "primary_p": primary_p,
            "primary_direction": primary_direction,
            "passes": primary_p < 0.05,
        })

    quarters_passing = sum(q["passes"] for q in quarters)
    pair_passes = quarters_passing == 4

    return {
        "sym_a": sym_a,
        "sym_b": sym_b,
        "adf_a": adf_a,
        "adf_b": adf_b,
        "eg": eg_primary,
        "eg_direction": eg_direction,
        "eg_reverse": eg_rev,
        "eg_reverse_direction": eg_rev_direction,
        "quarters": quarters,
        "quarters_passing": quarters_passing,
        "pair_passes": pair_passes,
    }


if __name__ == "__main__":
    results = run_all("NVDA", "TSM")
    print(f"ADF {results['sym_a']}: p={results['adf_a']['p_value']:.4f} {results['adf_a']['verdict']}")
    print(f"ADF {results['sym_b']}: p={results['adf_b']['p_value']:.4f} {results['adf_b']['verdict']}")
    print(f"\nQuarterly cointegration (EG primary direction p-value):")
    for q in results["quarters"]:
        icon = "✓" if q["passes"] else "✗"
        print(f"  {q['label']} ({q['start_date'].date()} → {q['end_date'].date()}): "
              f"p={q['primary_p']:.4f} [{q['primary_direction']}]  {icon}")
    print(f"\nQuarters passing: {results['quarters_passing']}/4")
    print(f"Pair passes (all 4): {results['pair_passes']}")
