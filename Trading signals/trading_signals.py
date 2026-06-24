import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import coint as eg_coint

load_dotenv()

WINDOW = 90        # rolling window for z-score mean/std (60–120 days, default 90)
BETA_WINDOW = 252  # trailing 1-year OLS window for quarterly β estimation (backtest only)


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


# ─── Compute half-life via AR(1) OLS on the spread ───────────────────────────

def compute_half_life(spread: pd.Series) -> float:
    """ΔSpread = c + θ·Spread_{t−1} + ε  →  half_life = −ln(2) / θ.
    Returns NaN if non-mean-reverting (θ ≥ 0) or insufficient data.
    """
    s = spread.dropna()
    if len(s) < 10:
        return np.nan
    delta = s.diff().dropna()
    lagged = s.shift(1).reindex(delta.index).dropna()
    aligned = pd.concat([delta, lagged], axis=1).dropna()
    if len(aligned) < 5:
        return np.nan
    try:
        model = OLS(aligned.iloc[:, 0].values, add_constant(aligned.iloc[:, 1].values)).fit()
        theta = float(model.params[1])
    except Exception:
        return np.nan
    if theta >= 0:
        return np.nan
    return -np.log(2) / theta


# ─── Volatility regime filter ─────────────────────────────────────────────────

def compute_volatility_regime(spread: pd.Series) -> pd.DataFrame:
    """σ_20 (EWMA) / σ_100 (rolling) ratio R.

    R < 1.3  → Normal (full size)
    1.3–1.8  → Elevated (halve position)
    ≥ 1.8    → Kill Switch (no new positions)
    """
    ewma_20 = spread.ewm(span=20, adjust=False).std()
    rolling_100 = spread.rolling(100).std()
    ratio = ewma_20 / rolling_100

    def _label(r):
        if pd.isna(r):
            return "Unknown"
        if r < 1.3:
            return "Normal"
        if r < 1.8:
            return "Elevated"
        return "Kill Switch"

    return pd.DataFrame({
        "ewma_20": ewma_20,
        "rolling_100": rolling_100,
        "ratio": ratio,
        "regime": ratio.apply(_label),
    }, index=spread.index)


# ─── Lightweight cointegration re-check (for monthly kill switch) ─────────────

def _check_cointegration_direction(a_slice: pd.Series, b_slice: pd.Series, direction: str) -> bool:
    """EG test in a specific direction on log prices. direction: 'AB' or 'BA'."""
    if len(a_slice) < 30:
        return False
    log_a = np.log(a_slice.values.astype(float))
    log_b = np.log(b_slice.values.astype(float))
    try:
        if direction == "AB":
            _, p, _ = eg_coint(log_a, log_b, trend="c", autolag="AIC")
        else:
            _, p, _ = eg_coint(log_b, log_a, trend="c", autolag="AIC")
    except Exception:
        return False
    return p < 0.05


# ─── New signal generation (cointegration-grounded) ───────────────────────────

def generate_signals(
    series_y: pd.Series,
    series_x: pd.Series,
    alpha_init: float,
    beta_init: float,
    est_window_start: pd.Timestamp,
    path: str = "path1",
    coint_direction: str = "AB",
    window: int = WINDOW,
) -> pd.DataFrame:
    """
    Cointegration-grounded pairs-trading signal generation.

    Parameters
    ----------
    series_y, series_x : price series for dependent (Y) and independent (X) stock.
        These must match the regression direction from the cointegration result:
        log(Y) = α + β·log(X) + ε
    alpha_init, beta_init : initial parameters from the cointegration window.
    est_window_start : start of the initial estimation window.
        Path 1 → approximately (today − 2yr); used as rolling 2yr lookback origin.
        Path 2 → ZA break date; window grows forward from this fixed start.
    path : 'path1' (rolling 2yr) or 'path2' (fixed start = ZA break date).
    coint_direction : 'AB' or 'BA' — which series is Y in the EG test order,
        used for monthly cointegration re-checks.
    window : z-score rolling window (60–120 days, default 90).

    Monthly re-estimation
    ---------------------
    At each calendar-month boundary the estimation OLS window is extended by 1 month
    and a fresh EG test is run:
    - Path 1: window = [current_date − 730 days, current_date]  (rolling 2yr)
    - Path 2: window = [est_window_start, current_date]          (growing from ZA break)
    If EG still passes → update α and β from the new OLS.
    If EG fails → activate cointegration kill switch (no new positions from that point).

    Kill switches
    -------------
    Any of the following disables new positions (current open positions stay until EXIT):
    1. Cointegration: monthly EG check fails.
    2. Half-life: rolling HL < 3, > 40, or doubled from initial estimate.
    3. β-drift: monthly re-estimated β (betas[i]) deviates > 20% from beta_init
       (the β at the time of the cointegration pass). This measures cumulative drift
       in the relationship since the pair was approved for trading — never a β
       estimated over a single month's window.
    4. Volatility: R_t = σ_20 / σ_100 ≥ 1.8.
    """
    # Slice to the trading window
    mask = series_y.index >= est_window_start
    sy = series_y[mask]
    sx = series_x[mask]

    if len(sy) < window + 10:
        sy = series_y
        sx = series_x

    n = len(sy)
    dates = sy.index
    y_vals = sy.values.astype(float)
    x_vals = sx.values.astype(float)
    log_y = np.log(y_vals)
    log_x = np.log(x_vals)

    # ── Monthly re-estimation: build time-varying α/β arrays ─────────────────
    month_periods = pd.PeriodIndex(dates, freq="M")
    month_change_idx = set(
        int(i) for i in np.where(month_periods != np.roll(month_periods, 1))[0]
        if i > 0
    )

    cur_alpha = alpha_init
    cur_beta = beta_init
    alphas = np.full(n, alpha_init)
    betas = np.full(n, beta_init)
    coint_stop = False
    coint_killed = np.zeros(n, dtype=bool)

    # We need full series for Path 1 rolling 2yr window lookback
    sy_full = series_y
    sx_full = series_x

    for i in range(1, n):
        if i in month_change_idx and not coint_stop:
            boundary_date = dates[i]

            if path == "path1":
                lookback_start = boundary_date - pd.Timedelta(days=730)
                a_win = sy_full[(sy_full.index >= lookback_start) & (sy_full.index < boundary_date)]
                b_win = sx_full[(sx_full.index >= lookback_start) & (sx_full.index < boundary_date)]
            else:
                a_win = sy_full[(sy_full.index >= est_window_start) & (sy_full.index < boundary_date)]
                b_win = sx_full[(sx_full.index >= est_window_start) & (sx_full.index < boundary_date)]

            if len(a_win) >= 30:
                still_passes = _check_cointegration_direction(a_win, b_win, coint_direction)
                if still_passes:
                    la = np.log(a_win.values.astype(float))
                    lb = np.log(b_win.values.astype(float))
                    x_ols = add_constant(lb) if coint_direction == "AB" else add_constant(la)
                    y_ols = la if coint_direction == "AB" else lb
                    try:
                        model = OLS(y_ols, x_ols).fit()
                        cur_alpha = float(model.params[0])
                        cur_beta = float(model.params[1])
                    except Exception:
                        pass
                else:
                    coint_stop = True

        alphas[i] = cur_alpha
        betas[i] = cur_beta
        if coint_stop:
            coint_killed[i] = True

    # ── Spread (log-space) ───────────────────────────────────────────────────
    spreads = log_y - (alphas + betas * log_x)
    spread_s = pd.Series(spreads, index=dates)

    # ── Frozen initial half-life ──────────────────────────────────────────────
    initial_half_life = compute_half_life(spread_s)

    # ── Rolling half-life (trailing 252d) ────────────────────────────────────
    rolling_hl = np.full(n, np.nan)
    hl_win = min(BETA_WINDOW, n - 1)
    for i in range(hl_win, n):
        chunk = spread_s.iloc[i - hl_win:i + 1]
        rolling_hl[i] = compute_half_life(chunk)
    rolling_hl_s = pd.Series(rolling_hl, index=dates)

    # ── Volatility regime ─────────────────────────────────────────────────────
    vol_df = compute_volatility_regime(spread_s)

    # ── Rolling β (trailing 252d OLS on log prices) — for chart display only ──
    roll_betas = np.full(n, np.nan)
    rb_win = min(BETA_WINDOW, n - 1)
    for i in range(rb_win, n):
        ly_w = log_y[i - rb_win:i + 1]
        lx_w = log_x[i - rb_win:i + 1]
        xm, ym = lx_w.mean(), ly_w.mean()
        denom = np.dot(lx_w - xm, lx_w - xm)
        if denom > 0:
            roll_betas[i] = np.dot(lx_w - xm, ly_w - ym) / denom

    # ── Kill switch flags ─────────────────────────────────────────────────────
    hl_arr = rolling_hl_s.values
    if not np.isnan(initial_half_life) and initial_half_life > 0:
        hl_kill = (
            (~np.isnan(hl_arr)) &
            ((hl_arr < 3) | (hl_arr > 40) | (hl_arr > 2 * initial_half_life))
        )
    else:
        hl_kill = np.zeros(n, dtype=bool)

    # β-drift: compare the monthly re-estimated β (betas[i]) against the initial β
    # from the cointegration pass (beta_init).  Never compare against a β estimated
    # over a single month's window — that would be noisy and inconsistent with the
    # cointegration-grounded parameter sourcing.
    beta_init_safe = beta_init if abs(beta_init) > 1e-9 else 1e-9
    beta_kill = np.abs(betas - beta_init) / abs(beta_init_safe) > 0.20

    vol_kill = np.array([r == "Kill Switch" for r in vol_df["regime"].values])

    kill_switch = coint_killed | hl_kill | beta_kill | vol_kill

    # ── Z-score ───────────────────────────────────────────────────────────────
    roll_mean = spread_s.rolling(window).mean()
    roll_std = spread_s.rolling(window).std()
    z_scores = (spread_s - roll_mean) / roll_std

    # ── Raw signal ────────────────────────────────────────────────────────────
    z = z_scores.values
    raw_signal = np.where(
        np.isnan(z), "HOLD",
        np.where(z < -2, "LONG",
        np.where(z > 2, "SHORT",
        np.where(np.abs(z) < 0.5, "EXIT", "HOLD")))
    )

    # ── Stateful positions with kill switch, HL gate, vol-regime sizing ───────
    position_y = np.zeros(n)
    position_x = np.zeros(n)
    cur_pos = 0.0

    for i in range(n):
        if np.isnan(betas[i]):
            continue

        if kill_switch[i]:
            cur_pos = 0.0
            position_y[i] = 0.0
            position_x[i] = 0.0
            continue

        sig = raw_signal[i]
        hl_ok = np.isnan(rolling_hl[i]) or (3 < rolling_hl[i] < 40)

        if sig == "LONG" and hl_ok:
            cur_pos = 1.0
        elif sig == "SHORT" and hl_ok:
            cur_pos = -1.0
        elif sig == "EXIT":
            cur_pos = 0.0
        # HOLD: unchanged

        size_mult = 0.5 if vol_df["regime"].iloc[i] == "Elevated" else 1.0
        position_y[i] = cur_pos * size_mult
        position_x[i] = -betas[i] * cur_pos * size_mult

    # ── Daily PnL (uses raw prices, not log) ─────────────────────────────────
    delta_y = pd.Series(y_vals, index=dates).diff()
    delta_x = pd.Series(x_vals, index=dates).diff()
    pos_y_s = pd.Series(position_y, index=dates)
    pos_x_s = pd.Series(position_x, index=dates)
    pnl = pos_y_s.shift(1) * delta_y + pos_x_s.shift(1) * delta_x

    df = pd.DataFrame({
        "price_y":      y_vals,
        "price_x":      x_vals,
        "alpha":        alphas,
        "beta":         betas,
        "spread":       spreads,
        "rolling_mean": roll_mean.values,
        "rolling_std":  roll_std.values,
        "z_score":      z_scores.values,
        "signal":       raw_signal,
        "position_y":   position_y,
        "position_x":   position_x,
        "delta_y":      delta_y.values,
        "delta_x":      delta_x.values,
        "pnl":          pnl.values,
        "half_life":    rolling_hl,
        "vol_ratio":    vol_df["ratio"].values,
        "vol_regime":   vol_df["regime"].values,
        "kill_switch":  kill_switch,
        "kill_coint":   coint_killed,
        "kill_hl":      hl_kill,
        "kill_beta":    beta_kill,
        "kill_vol":     vol_kill,
        "roll_beta":    roll_betas,
    }, index=dates)

    df["cumulative_pnl"] = df["pnl"].fillna(0).cumsum()
    return df


# ─── Legacy: quarterly-fixed β pipeline (used by Backtest) ───────────────────

def compute_rolling_signals(
    series_a: pd.Series, series_b: pd.Series, window: int = WINDOW
) -> pd.DataFrame:
    """
    Quarterly-fixed hedge ratio pairs-trading pipeline (instruction steps 1–8).
    Used by the Backtest tab. Trading Signals tab uses generate_signals() instead.

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

    quarter_labels = pd.PeriodIndex(dates, freq="Q")
    quarter_change_idx = set(
        int(i) for i in np.where(quarter_labels != np.roll(quarter_labels, 1))[0]
        if i >= BETA_WINDOW
    )
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

    quarter_str = [str(q) for q in quarter_labels]
    spreads = a_vals - (alphas + betas * b_vals)
    spread_s  = pd.Series(spreads, index=dates)
    roll_mean = spread_s.rolling(window).mean()
    roll_std  = spread_s.rolling(window).std()
    z_scores  = (spread_s - roll_mean) / roll_std

    z = z_scores.values
    raw_signal = np.where(
        z < -2, "LONG",
        np.where(z > 2, "SHORT",
        np.where(np.abs(z) < 0.5, "EXIT", "HOLD"))
    )

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
        position_a[i] = cur_pos_a
        position_b[i] = -betas[i] * cur_pos_a

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


def signal_translation(row, sym_y: str, sym_x: str) -> str:
    """Human-readable trade instruction from a generate_signals() row.
    sym_y = dependent stock (Y), sym_x = independent stock (X).
    """
    sig = row["signal"]
    beta = row["beta"]
    pos_col_y = "position_y" if "position_y" in row.index else "position_a"
    if sig == "LONG":
        return f"BUY 1 {sym_y}  |  SELL {abs(beta):.4f} {sym_x}"
    if sig == "SHORT":
        return f"SELL 1 {sym_y}  |  BUY {abs(beta):.4f} {sym_x}"
    if sig == "EXIT":
        return "EXIT — close all positions"
    return "HOLD — maintain current position"


if __name__ == "__main__":
    sa, sb = fetch_prices("JPM", "BAC")
    # Quick 2yr OLS for demo
    sa2 = sa.iloc[-504:]
    sb2 = sb.iloc[-504:]
    log_a = np.log(sa2.values.astype(float))
    log_b = np.log(sb2.values.astype(float))
    m = OLS(log_a, add_constant(log_b)).fit()
    alpha0, beta0 = float(m.params[0]), float(m.params[1])
    est_start = sa2.index[0]

    df = generate_signals(sa, sb, alpha0, beta0, est_start, path="path1")
    recent = df.dropna(subset=["z_score"]).tail(10)
    print(recent[["z_score", "signal", "half_life", "vol_regime", "kill_switch", "pnl"]].to_string())
    print(f"\nTotal PnL: {df['pnl'].sum():.2f}")
    print(f"Initial half-life: {compute_half_life(pd.Series(df['spread'].dropna())):.1f} days")
