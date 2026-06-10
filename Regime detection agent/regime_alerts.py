"""
Regime-change alert detection.

Takes the DataFrame from data_collector.fetch_indicators() and evaluates
11 rules across 5 indicator families. Returns a structured list of alert
dicts — one per rule — consumable directly by the Streamlit dashboard.

See PLAN.md for full rationale and threshold documentation.
"""
import pandas as pd

# ── Thresholds (edit here to retune) ───────────────────────────────────────────
TREASURY_ROC_THRESHOLD = 0.50   # 50 bps 20-day rate-of-change → alert
TIPS_LEVEL_THRESHOLD   = 1.00   # real yield crosses above 1% → alert
TIPS_MONTHLY_RISE      = 0.50   # real yield rises > 50 bps in 20 days → alert
CROSS_LOOKBACK         = 5      # trading days to look back for "recently crossed"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_latest(series: pd.Series, default: float | None = None) -> float | None:
    """Most recent non-NaN value, or default."""
    valid = series.dropna()
    return float(valid.iloc[-1]) if len(valid) > 0 else default


def _recently_crossed_above(
    a: pd.Series, b: pd.Series, window: int = CROSS_LOOKBACK
) -> bool:
    """True if `a` crossed above `b` within the last `window` rows."""
    both = a.notna() & b.notna()
    av, bv = a[both], b[both]
    if len(av) < 2:
        return False
    for i in range(1, min(window + 1, len(av))):
        if av.iloc[-i - 1] <= bv.iloc[-i - 1] and av.iloc[-i] > bv.iloc[-i]:
            return True
    return False


def _recently_crossed_below(
    a: pd.Series, b: pd.Series, window: int = CROSS_LOOKBACK
) -> bool:
    return _recently_crossed_above(b, a, window)


def _recently_crossed_threshold_above(
    series: pd.Series, threshold: float, window: int = CROSS_LOOKBACK
) -> bool:
    """True if series crossed above a scalar threshold within the last `window` rows."""
    valid = series.dropna()
    if len(valid) < 2:
        return False
    for i in range(1, min(window + 1, len(valid))):
        if valid.iloc[-i - 1] <= threshold < valid.iloc[-i]:
            return True
    return False


def _recently_crossed_threshold_below(
    series: pd.Series, threshold: float, window: int = CROSS_LOOKBACK
) -> bool:
    valid = series.dropna()
    if len(valid) < 2:
        return False
    for i in range(1, min(window + 1, len(valid))):
        if valid.iloc[-i - 1] >= threshold > valid.iloc[-i]:
            return True
    return False


# ── Main entry point ───────────────────────────────────────────────────────────

def detect_alerts(df: pd.DataFrame) -> list[dict]:
    """
    Evaluate all regime-change rules against the indicator DataFrame.

    Parameters
    ----------
    df : output of fetch_indicators(), indexed by date.

    Returns
    -------
    List of alert dicts.  Each dict:
        indicator        : readable name ("10Y Treasury Yield", …)
        rule             : short rule description
        triggered        : bool — condition is TRUE right now
        recently_crossed : bool — state changed within last 5 trading days
        severity         : "critical" | "warning" | "info"
        current          : current value of the primary series (float or None)
        signal           : threshold / MA value (float or None)
        message          : one-line human-readable status string
    """
    results: list[dict] = []

    def _add(indicator, rule, triggered, recently_crossed, severity, current, signal, message):
        results.append({
            "indicator": indicator,
            "rule": rule,
            "triggered": bool(triggered),
            "recently_crossed": bool(recently_crossed),
            "severity": severity,
            "current": current,
            "signal": signal,
            "message": message,
        })

    # ── 1. 10Y Treasury Yield ───────────────────────────────────────────────
    ty = df["treasury_10y"].dropna()

    if len(ty) >= 50:
        ma50 = ty.rolling(50).mean()
        cur, sig = _safe_latest(ty), _safe_latest(ma50)
        trig = cur > sig
        _add(
            "10Y Treasury Yield", "Yield above 50DMA",
            trig, _recently_crossed_above(ty, ma50),
            "warning" if trig else "info", cur, sig,
            f"Yield {cur:.3f}% {'>' if trig else '<'} 50DMA {sig:.3f}%",
        )

    if len(ty) >= 200:
        ma50  = ty.rolling(50).mean()
        ma200 = ty.rolling(200).mean()
        c50, c200 = _safe_latest(ma50), _safe_latest(ma200)
        trig = c50 > c200
        _add(
            "10Y Treasury Yield", "50DMA > 200DMA (bearish for bonds)",
            trig, _recently_crossed_above(ma50, ma200),
            "warning" if trig else "info", c50, c200,
            f"50DMA {c50:.3f}% {'>' if trig else '<'} 200DMA {c200:.3f}%",
        )

    if len(ty) >= 21:
        roc = float(ty.iloc[-1] - ty.iloc[-21])
        trig = roc > TREASURY_ROC_THRESHOLD
        _add(
            "10Y Treasury Yield",
            f"20-day rate of change > {TREASURY_ROC_THRESHOLD * 100:.0f} bps",
            trig, False,
            "warning" if trig else "info", roc, TREASURY_ROC_THRESHOLD,
            f"20-day ROC: {roc * 100:+.1f} bps  (threshold ±{TREASURY_ROC_THRESHOLD * 100:.0f} bps)",
        )

    # ── 2. Real Yields (10Y TIPS) ───────────────────────────────────────────
    tips = df["tips_10y"].dropna()

    if len(tips) >= 1:
        cur = _safe_latest(tips)
        trig = cur > TIPS_LEVEL_THRESHOLD
        _add(
            "10Y TIPS Real Yield", f"Real yield above {TIPS_LEVEL_THRESHOLD:.0f}%",
            trig, _recently_crossed_threshold_above(tips, TIPS_LEVEL_THRESHOLD),
            "warning" if trig else "info", cur, TIPS_LEVEL_THRESHOLD,
            f"Real yield {cur:.2f}% {'>' if trig else '<='} {TIPS_LEVEL_THRESHOLD:.0f}%",
        )

    if len(tips) >= 100:
        ma20  = tips.rolling(20).mean()
        ma100 = tips.rolling(100).mean()
        c20, c100 = _safe_latest(ma20), _safe_latest(ma100)
        trig = c20 > c100
        _add(
            "10Y TIPS Real Yield", "20DMA > 100DMA",
            trig, _recently_crossed_above(ma20, ma100),
            "warning" if trig else "info", c20, c100,
            f"20DMA {c20:.3f}% {'>' if trig else '<'} 100DMA {c100:.3f}%",
        )

    if len(tips) >= 21:
        rise = float(tips.iloc[-1] - tips.iloc[-21])
        trig = rise > TIPS_MONTHLY_RISE
        _add(
            "10Y TIPS Real Yield",
            f"Rose > {TIPS_MONTHLY_RISE * 100:.0f} bps in 20 days",
            trig, False,
            "warning" if trig else "info", rise, TIPS_MONTHLY_RISE,
            f"20-day change: {rise * 100:+.1f} bps  (threshold {TIPS_MONTHLY_RISE * 100:.0f} bps)",
        )

    # ── 3. Nasdaq-100 Breadth ───────────────────────────────────────────────
    breadth = df["nasdaq_breadth"].dropna()

    if len(breadth) >= 1:
        cur = _safe_latest(breadth)

        if cur >= 70:
            zone, sev = "STRONG (≥70%)", "info"
        elif cur >= 50:
            zone, sev = "NEUTRAL (50–70%)", "info"
        elif cur >= 30:
            zone, sev = "WARNING (<50%)", "warning"
        else:
            zone, sev = "SEVERE RISK-OFF (<30%)", "critical"

        _add(
            "Nasdaq-100 Breadth", f"Current zone: {zone}",
            cur < 50, False, sev, cur, None,
            f"{cur:.1f}% of NDX-100 stocks above 200DMA — {zone}",
        )

        if _recently_crossed_threshold_below(breadth, 50.0):
            _add(
                "Nasdaq-100 Breadth", "Recently crossed below 50%",
                True, True, "warning", cur, 50.0,
                f"Breadth dropped below 50% in the last {CROSS_LOOKBACK} days (now {cur:.1f}%)",
            )

        if cur < 30 and _recently_crossed_threshold_below(breadth, 30.0):
            _add(
                "Nasdaq-100 Breadth", "Recently crossed below 30%",
                True, True, "critical", cur, 30.0,
                f"Breadth dropped below 30% in the last {CROSS_LOOKBACK} days (now {cur:.1f}%)",
            )

    # ── 4. VIX Trend ────────────────────────────────────────────────────────
    vix = df["vix"].dropna()

    if len(vix) >= 100:
        ma20  = vix.rolling(20).mean()
        ma100 = vix.rolling(100).mean()
        c20, c100 = _safe_latest(ma20), _safe_latest(ma100)
        ratio = c20 / c100 if c100 else None
        trig = c20 > c100
        _add(
            "VIX Trend", "20DMA > 100DMA (rising volatility regime)",
            trig, _recently_crossed_above(ma20, ma100),
            "warning" if trig else "info", c20, c100,
            f"VIX 20DMA {c20:.2f} {'>' if trig else '<'} 100DMA {c100:.2f}"
            + (f"  (ratio {ratio:.3f})" if ratio else ""),
        )

    # ── 5. SMH/QQQ Relative Strength ────────────────────────────────────────
    ratio_s = df["smh_qqq_ratio"].dropna()

    if len(ratio_s) >= 100:
        ma100 = ratio_s.rolling(100).mean()
        cur, c100 = _safe_latest(ratio_s), _safe_latest(ma100)
        trig = cur < c100
        _add(
            "SMH/QQQ Relative Strength", "Ratio below 100DMA (semis lagging)",
            trig, _recently_crossed_below(ratio_s, ma100),
            "warning" if trig else "info", cur, c100,
            f"SMH/QQQ {cur:.4f} {'<' if trig else '>'} 100DMA {c100:.4f}",
        )

    if len(ratio_s) >= 200:
        ma50  = ratio_s.rolling(50).mean()
        ma200 = ratio_s.rolling(200).mean()
        c50, c200 = _safe_latest(ma50), _safe_latest(ma200)
        trig = c50 < c200
        _add(
            "SMH/QQQ Relative Strength", "50DMA < 200DMA (death cross)",
            trig, _recently_crossed_below(ma50, ma200),
            "critical" if trig else "info", c50, c200,
            f"50DMA {c50:.4f} {'<' if trig else '>'} 200DMA {c200:.4f}",
        )

    return results
