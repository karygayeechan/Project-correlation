import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.stattools import coint as eg_coint
from statsmodels.tsa.stattools import zivot_andrews

load_dotenv()

ROLLING_BETA_WINDOW = 252  # 1-year rolling window for stability diagnostics


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def fetch_prices(sym_a: str, sym_b: str, days: int = 365) -> tuple[pd.Series, pd.Series]:
    """Return adj_close Series for sym_a and sym_b over the past `days` calendar days."""
    end = date.today()
    start = end - timedelta(days=days)
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
    """ADF level + difference tests on log(price) to confirm I(1).

    Level:  adfuller(log_price, autolag='AIC') — expected p > 0.05
    Diff:   adfuller(log_returns, autolag='AIC') — expected p < 0.05
    I(1) = level non-stationary AND diff stationary.
    """
    log_series = np.log(series.dropna())

    res_level = adfuller(log_series, autolag="AIC")
    stat, p_value, crit = res_level[0], res_level[1], res_level[4]
    is_stationary = p_value < 0.05
    verdict = "?" if is_stationary else "✓"

    res_diff = adfuller(log_series.diff().dropna(), autolag="AIC")
    diff_p_value = res_diff[1]
    is_diff_stationary = diff_p_value < 0.05
    is_i1 = (not is_stationary) and is_diff_stationary

    return {
        "label": label,
        "stat": stat,
        "p_value": p_value,
        "critical_values": crit,
        "is_stationary": is_stationary,
        "verdict": verdict,
        "diff_p_value": diff_p_value,
        "is_diff_stationary": is_diff_stationary,
        "is_i1": is_i1,
    }


def run_engle_granger(series_a: pd.Series, series_b: pd.Series) -> dict:
    """Engle-Granger cointegration test on log prices.

    log(A) = α + β · log(B) + ε

    OLS gives α, β, and residuals for the spread chart.
    The p-value and critical values come from statsmodels.tsa.stattools.coint(), which uses
    MacKinnon (2010) EG-specific critical values — NOT plain ADF critical values.
    Plain adfuller() on OLS residuals uses the wrong distribution (MacKinnon 1994 for observed
    series), which underestimates p-values by comparing to unit-root critical values that are
    less negative than the EG-specific ones required for estimated residuals.
    """
    log_a = np.log(series_a.values.astype(float))
    log_b = np.log(series_b.values.astype(float))

    # OLS: α, β, residuals for the spread chart
    x = add_constant(log_b)
    model = OLS(log_a, x).fit()
    alpha = float(model.params[0])
    beta = float(model.params[1])
    residuals = pd.Series(model.resid, index=series_a.index, name="spread")

    # EG test: MacKinnon (2010) critical values, correct for estimated residuals
    coint_stat, p_value, crit_vals = eg_coint(log_a, log_b, trend="c", autolag="AIC")
    crit = {"1%": float(crit_vals[0]), "5%": float(crit_vals[1]), "10%": float(crit_vals[2])}

    is_cointegrated = p_value < 0.05
    return {
        "alpha": alpha,
        "beta": beta,
        "residuals": residuals,
        "stat": float(coint_stat),
        "p_value": float(p_value),
        "critical_values": crit,
        "is_cointegrated": is_cointegrated,
        "verdict": "✓" if is_cointegrated else "✗",
    }


def compute_rolling_beta(
    series_y: pd.Series, series_x: pd.Series, window: int = ROLLING_BETA_WINDOW
) -> pd.Series:
    """Rolling OLS β for log(Y) = α + β·log(X) using a trailing window.

    Computed separately from the EG tests — used only for stability diagnostics.
    Uses closed-form OLS formula to avoid repeated matrix construction overhead.
    """
    log_y = np.log(series_y.values.astype(float))
    log_x = np.log(series_x.values.astype(float))
    betas = np.full(len(log_y), np.nan)
    for i in range(window - 1, len(log_y)):
        y_w = log_y[i - window + 1 : i + 1]
        x_w = log_x[i - window + 1 : i + 1]
        xm, ym = x_w.mean(), y_w.mean()
        denom = np.dot(x_w - xm, x_w - xm)
        if denom > 0:
            betas[i] = np.dot(x_w - xm, y_w - ym) / denom
    return pd.Series(betas, index=series_y.index, name="rolling_beta")


def compute_rolling_eg_pvalue(
    series_y: pd.Series, series_x: pd.Series, window: int = ROLLING_BETA_WINDOW
) -> pd.Series:
    """Rolling 1yr EG p-value: at each date, OLS log(Y) on log(X)+const, ADF on residuals.

    Uses plain adfuller() (not coint()) for speed — this is for trend visualisation only,
    not for the final PASS/FAIL verdict. p < 0.05 band indicates cointegration at that point.
    """
    log_y = np.log(series_y.values.astype(float))
    log_x = np.log(series_x.values.astype(float))
    p_values = np.full(len(log_y), np.nan)
    for i in range(window - 1, len(log_y)):
        y_w = log_y[i - window + 1 : i + 1]
        x_w = log_x[i - window + 1 : i + 1]
        try:
            resid = OLS(y_w, add_constant(x_w)).fit().resid
            p_values[i] = adfuller(resid, autolag="AIC")[1]
        except Exception:
            pass
    return pd.Series(p_values, index=series_y.index, name="rolling_eg_pvalue")


def detect_structural_break(residuals: pd.Series) -> dict:
    """Zivot-Andrews test for a unit root allowing one unknown structural break.

    H0: unit root (no cointegration, even allowing a break)
    H1: stationary with a one-time level shift (cointegration interrupted by a break)

    Small p-value → reject H0 → spread IS stationary with a break → cointegration holds
    but was disrupted by a structural event at the detected break date.
    """
    clean = residuals.dropna()
    try:
        za_stat, za_pvalue, za_cvt, za_baselag, za_bp = zivot_andrews(
            clean.values, regression="c", autolag="t-stat"
        )
        break_date = clean.index[za_bp]
        return {
            "stat": float(za_stat),
            "pvalue": float(za_pvalue),
            "critical_values": {k: float(v) for k, v in za_cvt.items()},
            "break_date": break_date,
            "breakpoint_idx": int(za_bp),
            "is_break": za_pvalue < 0.05,
        }
    except Exception:
        return None


def run_eg_post_break(
    series_a: pd.Series, series_b: pd.Series, break_date, sym_a: str, sym_b: str
) -> dict | None:
    """Re-run EG in both directions on data strictly after the structural break date.

    Re-estimates α and β independently for each direction from the post-break window.
    Primary direction = lower p-value, same logic as the main 5yr/2yr tests.
    Returns None if fewer than 60 observations remain after the break.
    """
    a_post = series_a[series_a.index > break_date]
    b_post = series_b[series_b.index > break_date]
    if len(a_post) < 60:
        return None

    eg_ab = run_engle_granger(a_post, b_post)  # A = Y, B = X
    eg_ba = run_engle_granger(b_post, a_post)  # B = Y, A = X

    if eg_ab["p_value"] <= eg_ba["p_value"]:
        primary, reverse = eg_ab, eg_ba
        primary_direction   = f"{sym_a}→{sym_b}"
        reverse_direction   = f"{sym_b}→{sym_a}"
    else:
        primary, reverse = eg_ba, eg_ab
        primary_direction   = f"{sym_b}→{sym_a}"
        reverse_direction   = f"{sym_a}→{sym_b}"

    n_obs        = len(a_post)
    window_start = a_post.index[0]
    window_end   = a_post.index[-1]

    for d in (primary, reverse):
        d["n_obs"]        = n_obs
        d["window_start"] = window_start
        d["window_end"]   = window_end

    return {
        "primary":           primary,
        "reverse":           reverse,
        "primary_direction": primary_direction,
        "reverse_direction": reverse_direction,
        "n_obs":             n_obs,
        "window_start":      window_start,
        "window_end":        window_end,
    }


def identify_break_periods(
    rolling_pvalue: pd.Series, threshold: float = 0.05, min_days: int = 30
) -> list[dict]:
    """Scan rolling EG p-value series for contiguous stretches above threshold.

    Returns list of break periods sorted longest-first, filtered to >= min_days.
    Each dict has: start, end, days.
    """
    clean = rolling_pvalue.dropna()
    above = clean > threshold
    periods, in_break, bp_start = [], False, None
    for dt, is_above in above.items():
        if is_above and not in_break:
            in_break, bp_start = True, dt
        elif not is_above and in_break:
            in_break = False
            periods.append({"start": bp_start, "end": dt, "days": (dt - bp_start).days})
    if in_break:
        periods.append({"start": bp_start, "end": clean.index[-1],
                        "days": (clean.index[-1] - bp_start).days})
    periods = [p for p in periods if p["days"] >= min_days]
    periods.sort(key=lambda x: x["days"], reverse=True)
    return periods


def run_all(sym_a: str, sym_b: str) -> dict:
    """Run full cointegration analysis across three data windows.

    Each window estimates its own α and β independently:
      - 5yr: OLS on 5yr data — the long-run reference relationship
      - 2yr: OLS on 2yr data — compared against 5yr to detect structural drift
      - quarterly: OLS on each ~63-day window — display only, not used in verdict

    Verdict — a pair PASSES under either of two paths:

    Path 1 (standard): 5yr primary p < 0.05 AND 2yr primary p < 0.05 AND both primary
        regressions run in the same direction (e.g. both A→B). A pair whose 5yr test passes
        A→B while its 2yr test passes B→A has inconsistent directionality and does NOT pass.

    Path 2 (post-break): The post-break EG re-test passes (at least one direction p < 0.05)
        AND the ZA-detected structural break date is more than 2 years before today. This
        requires sufficient time in the new regime to have elapsed before trusting the result.
    """
    # ── 5yr: I(1) check + EG (α and β from 5yr OLS) ─────────────────────────
    series_a_5yr, series_b_5yr = fetch_prices(sym_a, sym_b, days=365 * 5)

    adf_a = run_adf(series_a_5yr, sym_a)
    adf_b = run_adf(series_b_5yr, sym_b)

    eg_ab_5yr = run_engle_granger(series_a_5yr, series_b_5yr)  # A = Y, B = X
    eg_ba_5yr = run_engle_granger(series_b_5yr, series_a_5yr)  # B = Y, A = X

    if eg_ab_5yr["p_value"] <= eg_ba_5yr["p_value"]:
        eg_primary, eg_rev = eg_ab_5yr, eg_ba_5yr
        eg_direction = f"{sym_a}→{sym_b}"
        eg_rev_direction = f"{sym_b}→{sym_a}"
        prim_y_5yr, prim_x_5yr = series_a_5yr, series_b_5yr
    else:
        eg_primary, eg_rev = eg_ba_5yr, eg_ab_5yr
        eg_direction = f"{sym_b}→{sym_a}"
        eg_rev_direction = f"{sym_a}→{sym_b}"
        prim_y_5yr, prim_x_5yr = series_b_5yr, series_a_5yr

    eg_5yr_passes = eg_primary["is_cointegrated"]

    # ── 2yr: fresh OLS on 2yr data — compare α, β to 5yr ────────────────────
    series_a_2yr, series_b_2yr = fetch_prices(sym_a, sym_b, days=365 * 2)

    eg_ab_2yr = run_engle_granger(series_a_2yr, series_b_2yr)  # A = Y, B = X
    eg_ba_2yr = run_engle_granger(series_b_2yr, series_a_2yr)  # B = Y, A = X

    if eg_ab_2yr["p_value"] <= eg_ba_2yr["p_value"]:
        eg_primary_2yr, eg_rev_2yr = eg_ab_2yr, eg_ba_2yr
        eg_direction_2yr = f"{sym_a}→{sym_b}"
        eg_rev_direction_2yr = f"{sym_b}→{sym_a}"
    else:
        eg_primary_2yr, eg_rev_2yr = eg_ba_2yr, eg_ab_2yr
        eg_direction_2yr = f"{sym_b}→{sym_a}"
        eg_rev_direction_2yr = f"{sym_a}→{sym_b}"

    eg_2yr_passes = eg_primary_2yr["is_cointegrated"]
    direction_match = eg_direction == eg_direction_2yr
    path1_passes = eg_5yr_passes and eg_2yr_passes and direction_match

    # ── 1yr quarterly: fresh OLS per quarter — display only ──────────────────
    series_a_1yr, series_b_1yr = fetch_prices(sym_a, sym_b, days=365)

    n = len(series_a_1yr)
    q_size = n // 4
    quarters = []
    for q in range(4):
        s = q * q_size
        e = n if q == 3 else (q + 1) * q_size
        a_q = series_a_1yr.iloc[s:e]
        b_q = series_b_1yr.iloc[s:e]

        eg_ab_q = run_engle_granger(a_q, b_q)  # fresh OLS for this quarter
        eg_ba_q = run_engle_granger(b_q, a_q)  # fresh OLS for this quarter

        ab_is_primary = eg_ab_q["p_value"] <= eg_ba_q["p_value"]
        primary_p = eg_ab_q["p_value"] if ab_is_primary else eg_ba_q["p_value"]
        primary_direction = f"{sym_a}→{sym_b}" if ab_is_primary else f"{sym_b}→{sym_a}"

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

    # ── Stability diagnostics: rolling β + rolling EG p-value (5yr primary) ──
    rolling_beta = compute_rolling_beta(prim_y_5yr, prim_x_5yr, window=ROLLING_BETA_WINDOW)
    rolling_eg_pvalue = compute_rolling_eg_pvalue(prim_y_5yr, prim_x_5yr, window=ROLLING_BETA_WINDOW)

    # ── Structural break analysis ─────────────────────────────────────────────
    sb = detect_structural_break(eg_primary["residuals"])
    break_periods = identify_break_periods(rolling_eg_pvalue)

    # Post-break re-test: start from the ZA-detected break date.
    # ZA identifies the single point where the structural break is most likely to have occurred.
    eg_post_break = None
    post_break_start_date = None
    if sb is not None:
        post_break_start_date = sb["break_date"]
        eg_post_break = run_eg_post_break(
            series_a_5yr, series_b_5yr, post_break_start_date, sym_a, sym_b
        )

    # ── Path 2: post-break pass AND ZA break date > 2 years ago ──────────────
    post_break_passes = False
    post_break_over_2yr = False
    if eg_post_break is not None and sb is not None:
        post_break_passes = (
            eg_post_break["primary"]["is_cointegrated"]
            or eg_post_break["reverse"]["is_cointegrated"]
        )
        days_since_break = (date.today() - sb["break_date"].date()).days
        post_break_over_2yr = days_since_break > 730

    path2_passes = post_break_passes and post_break_over_2yr
    pair_passes = path1_passes or path2_passes

    return {
        "sym_a": sym_a,
        "sym_b": sym_b,
        "adf_a": adf_a,
        "adf_b": adf_b,
        # 5yr EG (α, β from 5yr OLS)
        "eg": eg_primary,
        "eg_direction": eg_direction,
        "eg_reverse": eg_rev,
        "eg_reverse_direction": eg_rev_direction,
        "eg_5yr_passes": eg_5yr_passes,
        # Raw A→B and B→A results for both periods (used for β comparison)
        "eg_ab_5yr": eg_ab_5yr,
        "eg_ba_5yr": eg_ba_5yr,
        "eg_ab_2yr": eg_ab_2yr,
        "eg_ba_2yr": eg_ba_2yr,
        # 2yr EG (α, β from 2yr OLS — independent of 5yr)
        "eg_2yr": eg_primary_2yr,
        "eg_direction_2yr": eg_direction_2yr,
        "eg_reverse_2yr": eg_rev_2yr,
        "eg_reverse_direction_2yr": eg_rev_direction_2yr,
        "eg_2yr_passes": eg_2yr_passes,
        # Overall verdict
        "direction_match": direction_match,
        "path1_passes": path1_passes,
        "post_break_passes": post_break_passes,
        "post_break_over_2yr": post_break_over_2yr,
        "path2_passes": path2_passes,
        "pair_passes": pair_passes,
        # Quarterly (display only, fresh per-quarter OLS)
        "quarters": quarters,
        "quarters_passing": quarters_passing,
        # Stability diagnostics
        "rolling_beta": rolling_beta,
        "rolling_beta_direction": eg_direction,
        "rolling_beta_window": ROLLING_BETA_WINDOW,
        "rolling_eg_pvalue": rolling_eg_pvalue,
        # Structural break analysis
        "structural_break": sb,
        "break_periods": break_periods,
        "post_break_start_date": post_break_start_date,
        "eg_post_break": eg_post_break,
    }


if __name__ == "__main__":
    results = run_all("ARM", "TSM")
    a, b = results["adf_a"], results["adf_b"]
    print(f"ADF {results['sym_a']}: level p={a['p_value']:.4f} {a['verdict']}  diff p={a['diff_p_value']:.4f}  I(1)={'yes' if a['is_i1'] else 'NO'}")
    print(f"ADF {results['sym_b']}: level p={b['p_value']:.4f} {b['verdict']}  diff p={b['diff_p_value']:.4f}  I(1)={'yes' if b['is_i1'] else 'NO'}")

    print(f"\n5yr EG primary ({results['eg_direction']}): "
          f"α={results['eg']['alpha']:.4f}  β={results['eg']['beta']:.4f}  "
          f"p={results['eg']['p_value']:.4f}  {'✓' if results['eg_5yr_passes'] else '✗'}")
    print(f"5yr EG reverse ({results['eg_reverse_direction']}): "
          f"α={results['eg_reverse']['alpha']:.4f}  β={results['eg_reverse']['beta']:.4f}  "
          f"p={results['eg_reverse']['p_value']:.4f}")

    print(f"\n2yr EG primary ({results['eg_direction_2yr']}): "
          f"α={results['eg_2yr']['alpha']:.4f}  β={results['eg_2yr']['beta']:.4f}  "
          f"p={results['eg_2yr']['p_value']:.4f}  {'✓' if results['eg_2yr_passes'] else '✗'}")
    print(f"2yr EG reverse ({results['eg_reverse_direction_2yr']}): "
          f"α={results['eg_reverse_2yr']['alpha']:.4f}  β={results['eg_reverse_2yr']['beta']:.4f}  "
          f"p={results['eg_reverse_2yr']['p_value']:.4f}")

    sym_a, sym_b = results["sym_a"], results["sym_b"]
    ab5, ba5 = results["eg_ab_5yr"], results["eg_ba_5yr"]
    ab2, ba2 = results["eg_ab_2yr"], results["eg_ba_2yr"]
    print(f"\nβ comparison:")
    print(f"  {sym_a}→{sym_b}:  5yr β={ab5['beta']:.4f}  2yr β={ab2['beta']:.4f}  Δ={ab2['beta']-ab5['beta']:+.4f}")
    print(f"  {sym_b}→{sym_a}:  5yr β={ba5['beta']:.4f}  2yr β={ba2['beta']:.4f}  Δ={ba2['beta']-ba5['beta']:+.4f}")

    print(f"\nDirection match (5yr primary == 2yr primary): {results['direction_match']}")
    print(f"Path 1 PASS (5yr p<0.05 AND 2yr p<0.05 AND same direction): {results['path1_passes']}")
    print(f"Post-break passes: {results['post_break_passes']}  |  Break > 2yr ago: {results['post_break_over_2yr']}")
    print(f"Path 2 PASS (post-break pass AND break > 2yr ago): {results['path2_passes']}")
    print(f"Overall PASS (Path 1 OR Path 2): {results['pair_passes']}")

    print(f"\nQuarterly (fresh per-quarter OLS, display only):")
    for q in results["quarters"]:
        pdir = "★" if q["eg_ab"]["p_value"] <= q["eg_ba"]["p_value"] else " "
        print(f"  {q['label']} ({q['start_date'].date()} → {q['end_date'].date()}): "
              f"  {pdir}{sym_a}→{sym_b} β={q['eg_ab']['beta']:.3f} p={q['eg_ab']['p_value']:.4f}"
              f"  |  {'★' if pdir == ' ' else ' '}{sym_b}→{sym_a} β={q['eg_ba']['beta']:.3f} p={q['eg_ba']['p_value']:.4f}"
              f"  {'✓' if q['passes'] else '✗'}")
    print(f"\nQuarters with cointegration: {results['quarters_passing']}/4 (reference only)")

    rb = results["rolling_beta"].dropna()
    print(f"\nRolling β ({results['rolling_beta_window']}d, {results['rolling_beta_direction']}): "
          f"mean={rb.mean():.4f}  std={rb.std():.4f}  range=[{rb.min():.4f}, {rb.max():.4f}]")
