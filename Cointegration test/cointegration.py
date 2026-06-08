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
    """Return 5-year adj_close Series for sym_a and sym_b, aligned by date."""
    end = date.today()
    start = end - timedelta(days=5 * 365)
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
    """Run full cointegration analysis and return all results."""
    series_a, series_b = fetch_prices(sym_a, sym_b)
    adf_a = run_adf(series_a, sym_a)
    adf_b = run_adf(series_b, sym_b)
    eg = run_engle_granger(series_a, series_b)

    # All four criteria must hold for the pair to pass
    pair_passes = (
        not adf_a["is_stationary"]   # A is non-stationary
        and not adf_b["is_stationary"]  # B is non-stationary
        and eg["is_cointegrated"]       # spread is stationary
    )

    return {
        "sym_a": sym_a,
        "sym_b": sym_b,
        "adf_a": adf_a,
        "adf_b": adf_b,
        "eg": eg,
        "pair_passes": pair_passes,
    }


if __name__ == "__main__":
    results = run_all("NVDA", "TSM")
    print(f"ADF {results['sym_a']}: p={results['adf_a']['p_value']:.4f} {results['adf_a']['verdict']}")
    print(f"ADF {results['sym_b']}: p={results['adf_b']['p_value']:.4f} {results['adf_b']['verdict']}")
    print(f"Engle-Granger: beta={results['eg']['beta']:.4f}, p={results['eg']['p_value']:.4f} {results['eg']['verdict']}")
    print(f"Pair passes: {results['pair_passes']}")
