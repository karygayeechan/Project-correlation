import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import adfuller
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Trading signals"))
from trading_signals import fetch_prices, compute_rolling_signals


def get_split_dates():
    """Return (train_start, train_end, test_start, test_end) for the 4y/1y split."""
    today = date.today()
    test_end = today
    test_start = today - timedelta(days=365)
    train_end = test_start
    train_start = today - timedelta(days=5 * 365)
    return train_start, train_end, test_start, test_end


def run_backtest(sym_a: str, sym_b: str, window: int = 90):
    """
    Fetch 5y of prices, run quarterly-fixed-β signals on full history, then slice
    out the test period (most recent 1y) for evaluation.

    β is estimated from a trailing 1-year OLS refreshed at each calendar-quarter
    boundary (see compute_rolling_signals). `window` controls only the z-score
    rolling mean/std (60–120 days, default 90). The 4-year training period warms up
    the β estimation so every quarter in the test window has a fully calibrated β.

    Returns (full_df, test_df). DB is not modified.
    """
    series_a, series_b = fetch_prices(sym_a, sym_b)
    full_df = compute_rolling_signals(series_a, series_b, window=window)
    _, _, test_start, test_end = get_split_dates()
    test_df = full_df.loc[pd.Timestamp(test_start):pd.Timestamp(test_end)].copy()
    return full_df, test_df


def identify_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    Group continuous non-zero position blocks into discrete trades.
    Handles mid-stream flips (LONG→SHORT without EXIT).
    """
    df = df.dropna(subset=["pnl"]).copy()
    trades = []
    in_trade = False
    entry_date = None
    trade_pnl = 0.0
    direction = None
    prev_pos = 0.0

    for date_i, row in df.iterrows():
        cur_pos = float(row["position_a"])
        pnl_i = float(row["pnl"]) if not np.isnan(float(row["pnl"])) else 0.0

        if not in_trade and cur_pos != 0:
            in_trade = True
            entry_date = date_i
            trade_pnl = pnl_i
            direction = "LONG" if cur_pos > 0 else "SHORT"
        elif in_trade and cur_pos == 0:
            trades.append({
                "entry_date": entry_date,
                "exit_date": date_i,
                "direction": direction,
                "holding_days": (date_i - entry_date).days,
                "pnl": trade_pnl,
            })
            in_trade = False
            trade_pnl = 0.0
            direction = None
        elif in_trade and cur_pos != 0 and prev_pos != 0 and np.sign(cur_pos) != np.sign(prev_pos):
            # Flip without EXIT: close current, open new
            trades.append({
                "entry_date": entry_date,
                "exit_date": date_i,
                "direction": direction,
                "holding_days": (date_i - entry_date).days,
                "pnl": trade_pnl,
            })
            entry_date = date_i
            trade_pnl = pnl_i
            direction = "LONG" if cur_pos > 0 else "SHORT"
        elif in_trade:
            trade_pnl += pnl_i

        prev_pos = cur_pos

    if in_trade:
        trades.append({
            "entry_date": entry_date,
            "exit_date": df.index[-1],
            "direction": direction,
            "holding_days": (df.index[-1] - entry_date).days,
            "pnl": trade_pnl,
        })

    if not trades:
        return pd.DataFrame(columns=["entry_date", "exit_date", "direction", "holding_days", "pnl"])
    return pd.DataFrame(trades)


def compute_halflife(spread: pd.Series) -> float:
    """
    Estimate mean-reversion half-life via OLS on AR(1) differences:
      Δspread = γ · spread_{t-1} + ε   →   half_life = −ln(2) / γ
    Returns nan if series is not mean-reverting (γ ≥ 0).
    """
    spread = spread.dropna()
    if len(spread) < 10:
        return np.nan
    delta = spread.diff().dropna()
    lagged = spread.shift(1).reindex(delta.index).dropna()
    aligned = pd.concat([delta, lagged], axis=1).dropna()
    if len(aligned) < 5:
        return np.nan
    try:
        model = OLS(aligned.iloc[:, 0].values, add_constant(aligned.iloc[:, 1].values)).fit()
        gamma = float(model.params[1])
    except Exception:
        return np.nan
    if gamma >= 0:
        return np.nan
    return -np.log(2) / gamma


def _sharpe_label(s: float) -> str:
    if s < 0.5:  return "Bad (<0.5)"
    if s < 1.0:  return "Weak (0.5–1.0)"
    if s < 2.0:  return "Decent (1.0–2.0)"
    return "Strong (>2.0)"


def _calmar_label(c) -> str:
    if c is None or np.isnan(c): return "N/A"
    if c < 0.5:  return "Weak (<0.5)"
    if c < 1.0:  return "Acceptable (0.5–1.0)"
    if c < 2.0:  return "Good (1.0–2.0)"
    if c < 3.0:  return "Very Good (2.0–3.0)"
    return "Excellent — verify for overfit (>3.0)"


def _halflife_label(h) -> str:
    if h is None or (isinstance(h, float) and np.isnan(h)): return "N/A"
    if h < 3:   return "Too fast / noisy (<3 days)"
    if h < 20:  return "Ideal stat-arb zone (5–20 days)"
    if h < 50:  return "Slower swing trades (20–50 days)"
    return "Capital inefficient (>50 days)"


def compute_all_metrics(test_df: pd.DataFrame) -> dict:
    """
    Compute all backtest metrics on a pre-sliced test-period DataFrame.
    All time-series are confined to the test window; nothing touches the DB.
    """
    valid = test_df.dropna(subset=["z_score"]).copy()
    daily_pnl = valid["pnl"].fillna(0)
    active_mask = valid["position_a"].shift(1).fillna(0) != 0
    active_pnl = daily_pnl[active_mask]

    # Cumulative PnL from test-period start (not from all-time history)
    cum_pnl = daily_pnl.cumsum()
    drawdown = cum_pnl - cum_pnl.cummax()

    capital = float(valid["price_a"].mean()) if "price_a" in valid.columns else 1.0

    # ── Performance ───────────────────────────────────────────────────────────
    total_pnl = float(daily_pnl.sum())
    n_test_days = len(valid)
    ann_pnl = float(daily_pnl.mean() * 252)
    ann_return_pct = (ann_pnl / capital * 100) if capital > 0 else 0.0
    max_drawdown = float(drawdown.min())

    pnl_std = float(daily_pnl.std())
    sharpe = (float(daily_pnl.mean()) / pnl_std * np.sqrt(252)) if pnl_std > 0 else 0.0

    quarterly_sharpe = {}
    for q, grp in daily_pnl.groupby(daily_pnl.index.to_period("Q")):
        qs = (grp.mean() / grp.std() * np.sqrt(252)) if grp.std() > 0 else 0.0
        quarterly_sharpe[str(q)] = round(float(qs), 3)

    rolling_sharpe_30 = daily_pnl.rolling(30).apply(
        lambda x: x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0.0, raw=True
    )
    rolling_sharpe_60 = daily_pnl.rolling(60).apply(
        lambda x: x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0.0, raw=True
    )

    calmar = (ann_pnl / abs(max_drawdown)) if max_drawdown != 0 else None

    trades_df = identify_trades(valid)
    n_trades = len(trades_df)
    pct5_trade  = float(trades_df["pnl"].quantile(0.05)) if n_trades > 0 else None
    pct95_trade = float(trades_df["pnl"].quantile(0.95)) if n_trades > 0 else None
    avg_profit  = float(trades_df["pnl"].mean())         if n_trades > 0 else None
    avg_holding = float(trades_df["holding_days"].mean()) if n_trades > 0 else None
    trade_pnl_std = float(trades_df["pnl"].std())        if n_trades > 1 else None

    win_rate = float((active_pnl > 0).mean() * 100) if len(active_pnl) > 0 else 0.0

    # ── Trading Activity ──────────────────────────────────────────────────────
    spread_series = valid["spread"].dropna()
    halflife = compute_halflife(spread_series)

    price_a = valid["price_a"] if "price_a" in valid.columns else pd.Series(capital, index=valid.index)
    price_b = valid["price_b"] if "price_b" in valid.columns else pd.Series(capital, index=valid.index)
    turnover = (
        valid["position_a"].diff().abs() * price_a +
        valid["position_b"].diff().abs() * price_b
    ).fillna(0)
    total_turnover = float(turnover.sum())

    cost_scenarios = {}
    for bps in [0, 1, 5, 10]:
        adj = daily_pnl - turnover * bps / 10000
        s = (adj.mean() / adj.std() * np.sqrt(252)) if adj.std() > 0 else 0.0
        cost_scenarios[bps] = round(float(s), 3)

    s0, s10 = cost_scenarios[0], cost_scenarios[10]
    if s10 < 0:
        cost_label = "Bad — Sharpe turns negative under 10 bps stress test"
    elif s0 > 0 and abs(s0 - s10) / max(abs(s0), 1e-9) < 0.25:
        cost_label = "Good — Sharpe is stable under realistic transaction costs"
    else:
        cost_label = "Moderate — Sharpe degrades materially; cost-sensitive strategy"

    # ── Risk ──────────────────────────────────────────────────────────────────
    pnl_arr = daily_pnl.dropna().values
    vol_ann = float(daily_pnl.std() * np.sqrt(252))
    skewness = float(stats.skew(pnl_arr))
    kurtosis = float(stats.kurtosis(pnl_arr))
    var_95 = float(np.percentile(pnl_arr, 5))
    below_var = pnl_arr[pnl_arr <= var_95]
    cvar_95 = float(below_var.mean()) if len(below_var) > 0 else var_95

    max_streak_days = cur_days = 0
    max_streak_val = cur_val = 0.0
    for p in daily_pnl.values:
        if p < 0:
            cur_days += 1
            cur_val += p
        else:
            if cur_days > max_streak_days:
                max_streak_days, max_streak_val = cur_days, cur_val
            cur_days, cur_val = 0, 0.0
    if cur_days > max_streak_days:
        max_streak_days, max_streak_val = cur_days, cur_val

    # ── Stability ─────────────────────────────────────────────────────────────
    adf_win = 60
    rolling_adf_p, rolling_adf_idx = [], []
    for i in range(adf_win, len(spread_series) + 1):
        chunk = spread_series.iloc[i - adf_win:i]
        try:
            p = adfuller(chunk, autolag="AIC")[1]
        except Exception:
            p = np.nan
        rolling_adf_p.append(p)
        rolling_adf_idx.append(spread_series.index[i - 1])
    rolling_adf = pd.Series(rolling_adf_p, index=rolling_adf_idx, name="adf_p")

    hl_win = 60
    rolling_hl, rolling_hl_idx = [], []
    for i in range(hl_win, len(spread_series) + 1):
        chunk = spread_series.iloc[i - hl_win:i]
        rolling_hl.append(compute_halflife(chunk))
        rolling_hl_idx.append(spread_series.index[i - 1])
    rolling_halflife = pd.Series(rolling_hl, index=rolling_hl_idx, name="halflife")

    # ── Scalability ───────────────────────────────────────────────────────────
    scale_results = {}
    for scale in [1, 2, 5]:
        sp = daily_pnl * scale
        sp_cum = sp.cumsum()
        sp_dd = float((sp_cum - sp_cum.cummax()).min())
        sp_s = (sp.mean() / sp.std() * np.sqrt(252)) if sp.std() > 0 else 0.0
        sp_wr = float((active_pnl * scale > 0).mean() * 100) if len(active_pnl) > 0 else 0.0
        scale_results[scale] = {
            "sharpe": round(float(sp_s), 3),
            "max_dd": round(sp_dd, 2),
            "win_rate": round(sp_wr, 1),
            "ann_pnl": round(float(sp.mean() * 252), 2),
            "total_pnl": round(float(sp.sum()), 2),
        }

    return {
        "total_pnl": round(total_pnl, 2),
        "ann_pnl": round(ann_pnl, 2),
        "ann_return_pct": round(ann_return_pct, 2),
        "capital_proxy": round(capital, 2),
        "pct5_trade_pnl": round(pct5_trade, 2) if pct5_trade is not None else None,
        "pct95_trade_pnl": round(pct95_trade, 2) if pct95_trade is not None else None,
        "sharpe": round(sharpe, 3),
        "sharpe_label": _sharpe_label(sharpe),
        "quarterly_sharpe": quarterly_sharpe,
        "rolling_sharpe_30": rolling_sharpe_30,
        "rolling_sharpe_60": rolling_sharpe_60,
        "cum_pnl": cum_pnl,
        "drawdown": drawdown,
        "max_drawdown": round(max_drawdown, 2),
        "calmar": round(float(calmar), 3) if calmar is not None else None,
        "calmar_label": _calmar_label(calmar),
        "win_rate": round(win_rate, 1),
        "avg_profit_per_trade": round(avg_profit, 2) if avg_profit is not None else None,
        "n_trades": n_trades,
        "trades_df": trades_df,
        "avg_holding": round(avg_holding, 1) if avg_holding is not None else None,
        "halflife": round(float(halflife), 1) if halflife is not None and not np.isnan(halflife) else None,
        "halflife_label": _halflife_label(halflife),
        "total_turnover": round(total_turnover, 2),
        "cost_scenarios": cost_scenarios,
        "cost_label": cost_label,
        "vol_ann": round(vol_ann, 2),
        "skewness": round(skewness, 3),
        "kurtosis": round(kurtosis, 3),
        "var_95": round(var_95, 2),
        "cvar_95": round(cvar_95, 2),
        "max_losing_streak_days": max_streak_days,
        "max_losing_streak_val": round(max_streak_val, 2),
        "spread_series": spread_series,
        "rolling_adf": rolling_adf,
        "zscore_vals": valid["z_score"].dropna(),
        "beta_series": valid["beta"].dropna(),
        "rolling_halflife": rolling_halflife,
        "trade_pnl_std": round(trade_pnl_std, 2) if trade_pnl_std is not None else None,
        "scale_results": scale_results,
        "daily_pnl": daily_pnl,
        "n_test_days": n_test_days,
        "n_active_days": int(active_mask.sum()),
    }


if __name__ == "__main__":
    _, test_df = run_backtest("AAPL", "GOOGL")
    m = compute_all_metrics(test_df)
    print(f"Test period: {test_df.index[0].date()} → {test_df.index[-1].date()}, n={m['n_test_days']}")
    print(f"Total PnL:   ${m['total_pnl']:.2f}")
    print(f"Sharpe:      {m['sharpe']:.3f}  ({m['sharpe_label']})")
    print(f"Max DD:      ${m['max_drawdown']:.2f}")
    print(f"Calmar:      {m['calmar']}  ({m['calmar_label']})")
    print(f"Win Rate:    {m['win_rate']:.1f}%")
    print(f"N Trades:    {m['n_trades']}")
    print(f"Half-life:   {m['halflife']} days  ({m['halflife_label']})")
