import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

load_dotenv()

WINDOW = 90        # rolling window for z-score mean/std (60–120 days, default 90)
BETA_WINDOW = 252  # trailing 1-year OLS window for quarterly β estimation


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


def compute_rolling_signals(
    series_a: pd.Series, series_b: pd.Series, window: int = WINDOW
) -> pd.DataFrame:
    """
    Quarterly-fixed hedge ratio pairs-trading pipeline (instruction steps 1–8).

    Step 1: z-score window = `window` days (60–120, default 90)
    Step 2: β estimated from trailing 1-year OLS (252 days), refreshed at each
            calendar-quarter boundary — fixed for the entire quarter, no daily drift
    Step 3: spread_t = A_t − (α_q + β_q × B_t)  using the quarter's fixed α/β
    Step 4: z_t = (spread_t − μ_t) / σ_t  where μ/σ roll over `window` days
    Step 5: z < −2 → LONG, z > 2 → SHORT, |z| < 0.5 → EXIT, else HOLD
    Step 6: LONG → buy 1 A / sell β B; SHORT → sell 1 A / buy β B
    Step 7: position_B = −β_q × position_A  (β_q is the quarter's fixed value)
    Step 8: PnL_t = pos_A_{t−1} × ΔA_t + pos_B_{t−1} × ΔB_t
    """
    n = len(series_a)
    dates = series_a.index
    a_vals = series_a.values.astype(float)
    b_vals = series_b.values.astype(float)

    # Step 2: quarterly-fixed β — recomputed at each calendar quarter boundary
    # using a trailing 1-year (BETA_WINDOW) OLS.  Requires BETA_WINDOW days of
    # history before the first β can be estimated, so early rows stay NaN.
    quarter_labels = pd.PeriodIndex(dates, freq="Q")
    quarter_change_idx = set(
        int(i) for i in np.where(quarter_labels != np.roll(quarter_labels, 1))[0]
        if i >= BETA_WINDOW
    )
    # Always compute at the first eligible day too
    quarter_change_idx.add(BETA_WINDOW)

    alphas = np.full(n, np.nan)
    betas  = np.full(n, np.nan)
    cur_alpha, cur_beta = np.nan, np.nan

    for i in range(BETA_WINDOW, n):
        if i in quarter_change_idx:
            a_w = a_vals[i - BETA_WINDOW:i]
            b_w = b_vals[i - BETA_WINDOW:i]
            x = add_constant(b_w)
            model = OLS(a_w, x).fit()
            cur_alpha = float(model.params[0])
            cur_beta  = float(model.params[1])
        alphas[i] = cur_alpha
        betas[i]  = cur_beta

    # Carry a "quarter_label" column so the UI can show which β is active
    quarter_str = [str(q) for q in quarter_labels]

    # Step 3: spread using fixed quarterly α/β
    spreads = a_vals - (alphas + betas * b_vals)

    # Step 4: z-score — rolling mean/std over `window` days (60–120)
    spread_s  = pd.Series(spreads, index=dates)
    roll_mean = spread_s.rolling(window).mean()
    roll_std  = spread_s.rolling(window).std()
    z_scores  = (spread_s - roll_mean) / roll_std

    # Step 5: raw signal
    z = z_scores.values
    raw_signal = np.where(
        z < -2, "LONG",
        np.where(z > 2, "SHORT",
        np.where(np.abs(z) < 0.5, "EXIT", "HOLD"))
    )

    # Steps 6 & 7: stateful positions; β is the fixed quarterly value
    position_a = np.zeros(n)
    position_b = np.zeros(n)
    cur_pos_a  = 0.0

    for i in range(n):
        if np.isnan(betas[i]):
            continue
        sig = raw_signal[i]
        if sig == "LONG":
            cur_pos_a = 1.0
        elif sig == "SHORT":
            cur_pos_a = -1.0
        elif sig == "EXIT":
            cur_pos_a = 0.0
        # HOLD: keep cur_pos_a unchanged; β is the current quarter's fixed value
        position_a[i] = cur_pos_a
        position_b[i] = -betas[i] * cur_pos_a

    # Step 8: daily PnL
    delta_a = pd.Series(a_vals, index=dates).diff()
    delta_b = pd.Series(b_vals, index=dates).diff()
    pos_a_s = pd.Series(position_a, index=dates)
    pos_b_s = pd.Series(position_b, index=dates)
    pnl = pos_a_s.shift(1) * delta_a + pos_b_s.shift(1) * delta_b

    df = pd.DataFrame({
        "price_a":      a_vals,
        "price_b":      b_vals,
        "quarter":      quarter_str,
        "alpha":        alphas,
        "beta":         betas,
        "spread":       spreads,
        "rolling_mean": roll_mean.values,
        "rolling_std":  roll_std.values,
        "z_score":      z_scores.values,
        "signal":       raw_signal,
        "position_a":   position_a,
        "position_b":   position_b,
        "delta_a":      delta_a.values,
        "delta_b":      delta_b.values,
        "pnl":          pnl.values,
    }, index=dates)

    df["cumulative_pnl"] = df["pnl"].fillna(0).cumsum()
    return df


def signal_translation(row, sym_a: str, sym_b: str) -> str:
    """Human-readable translation of a signal row (step 6 output)."""
    sig = row["signal"]
    beta = row["beta"]
    if sig == "LONG":
        return f"BUY 1 {sym_a}  |  SELL {abs(beta):.4f} {sym_b}"
    if sig == "SHORT":
        return f"SELL 1 {sym_a}  |  BUY {abs(beta):.4f} {sym_b}"
    if sig == "EXIT":
        return "EXIT — close all positions"
    return "HOLD — maintain current position"


if __name__ == "__main__":
    series_a, series_b = fetch_prices("NVDA", "TSM")
    df = compute_rolling_signals(series_a, series_b)
    recent = df.dropna(subset=["z_score"]).tail(10)
    print(recent[["z_score", "signal", "position_a", "position_b", "pnl"]].to_string())
    print(f"\nTotal PnL: {df['pnl'].sum():.2f}")
