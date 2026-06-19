"""
Alert engine for quarterly fundamental comparisons.
Entry point: detect_fundamental_alerts(data_a, data_b) -> list[dict]

Three layers:
  Layer 1  — Pair relative-shift: how A-vs-B gap changed QoQ
  Layer 2a — Individual trend: each stock vs its own prior quarters (QoQ/YoY)
  Layer 2b — Earnings quality / red flags: 17 pattern and ratio-based checks
"""
import numpy as np
import pandas as pd

# ── Layer 1 thresholds ────────────────────────────────────────────────────────
PAIR_REVENUE_SHIFT        = 0.15
PAIR_NET_INCOME_SHIFT     = 0.20
PAIR_GROSS_MARGIN_SHIFT   = 0.08
PAIR_OP_MARGIN_SHIFT      = 0.08
PAIR_NET_MARGIN_SHIFT     = 0.08
PAIR_FCF_SHIFT            = 0.20
PAIR_DE_RATIO_SHIFT       = 0.30
PAIR_CURRENT_RATIO_SHIFT  = 0.50
PAIR_RD_RATIO_SHIFT       = 0.10

# ── Layer 2a thresholds ───────────────────────────────────────────────────────
INDIV_REVENUE_DROP_QOQ      = -0.10
INDIV_REVENUE_SURGE_QOQ     =  0.30
INDIV_NET_INCOME_SWING_QOQ  =  0.20
INDIV_OP_MARGIN_DROP_QOQ    = -0.05
INDIV_FCF_DROP_QOQ          = -0.25
INDIV_CASH_DROP_QOQ         = -0.20
INDIV_DEBT_SURGE_QOQ        =  0.25
INDIV_EPS_DROP_QOQ          = -0.15
INDIV_REVENUE_DROP_YOY      = -0.05
INDIV_NET_INCOME_DROP_YOY   = -0.25
INDIV_FCF_DROP_YOY          = -0.30

# ── Layer 2b thresholds ───────────────────────────────────────────────────────
QUALITY_DSO_RISE_QTRS        = 2
QUALITY_NI_WITHOUT_REV_NI    = 0.10
QUALITY_REV_VS_OCF_REV_YOY   = 0.10
QUALITY_OPEX_REV_GAP_QOQ     = 0.15
QUALITY_CASH_DECLINE_QTRS    = 3
QUALITY_NI_OCF_QTRS          = 3
QUALITY_ACCRUAL_RATIO        = 0.10
QUALITY_OCF_NI_RATIO_LOW     = 0.75
QUALITY_DEBT_YOY             = 1.00
QUALITY_INTANGIBLES_YOY      = 0.30
QUALITY_CAPEX_COLLAPSE_QOQ   = -0.40
QUALITY_NONOP_INCOME_RATIO   = 0.10
QUALITY_INVENTORY_DAYS_RISE  = 15
QUALITY_INVENTORY_RISE_QTRS  = 2
QUALITY_DEBT_WITH_PROFITS_QOQ = 0.15
QUALITY_EPS_CASH_EPS_QOQ     = 0.10
QUALITY_EPS_CASH_FCF_QOQ     = 0.05

# ── Pattern → Potential Concern labels ───────────────────────────────────────
CONCERN = {
    "AR outpacing revenue":        "Aggressive revenue recognition",
    "Inventory buildup":           "Weak demand",
    "NI > OCF repeatedly":         "Low earnings quality",
    "Low OCF/NI ratio":            "Low earnings quality",
    "Negative OCF with profits":   "Low earnings quality",
    "Profits with rising debt":    "Profit not translating into cash",
    "Intangibles surge":           "Overpayment risk",
    "EPS up, cash flat":           "Financial engineering",
    "External funding dependence": "Dependence on external funding",
}

# Pair metrics: (display_name, stmt_key, col, threshold, is_absolute, base_severity)
# is_absolute=True  → diff = A - B  (used for ratios/margins already on 0–1 or ratio scale)
# is_absolute=False → diff = (A - B) / |B|  (used for dollar amounts)
PAIR_METRICS = [
    ("Revenue",          "income",   "Revenue",          PAIR_REVENUE_SHIFT,       False, "Critical"),
    ("Net Income",       "income",   "Net Income",       PAIR_NET_INCOME_SHIFT,    False, "Critical"),
    ("Gross Margin",     "derived",  "Gross Margin",     PAIR_GROSS_MARGIN_SHIFT,  True,  "Warning"),
    ("Operating Margin", "derived",  "Operating Margin", PAIR_OP_MARGIN_SHIFT,     True,  "Warning"),
    ("Net Margin",       "derived",  "Net Margin",       PAIR_NET_MARGIN_SHIFT,    True,  "Warning"),
    ("FCF",              "cashflow", "FCF",              PAIR_FCF_SHIFT,           False, "Critical"),
    ("D/E Ratio",        "derived",  "D/E Ratio",        PAIR_DE_RATIO_SHIFT,      True,  "Warning"),
    ("Current Ratio",    "derived",  "Current Ratio",    PAIR_CURRENT_RATIO_SHIFT, True,  "Warning"),
    ("R&D % Revenue",    "derived",  "R&D % Revenue",    PAIR_RD_RATIO_SHIFT,      True,  "Info"),
]

MARGIN_COLS = {"Gross Margin", "Operating Margin", "Net Margin", "FCF Margin", "R&D % Revenue"}

_SEV_ORDER = {"Critical": 0, "Warning": 1, "Info": 2}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(new, old) -> float:
    if pd.isna(new) or pd.isna(old) or old == 0:
        return float("nan")
    return (new - old) / abs(old)


def _v(series, idx: int) -> float:
    try:
        val = series.iloc[idx]
        return float(val) if not pd.isna(val) else float("nan")
    except (IndexError, TypeError):
        return float("nan")


def _s(data: dict, stmt: str, col: str) -> pd.Series:
    df = data.get(stmt, pd.DataFrame())
    if df.empty or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _make(layer: str, **kw) -> dict:
    alert = {"layer": layer, "triggered": True}
    alert.update(kw)
    concern = CONCERN.get(kw.get("flag", ""), None)
    alert.setdefault("concern", concern)
    if concern and alert.get("message"):
        alert["message"] = f"{alert['message']} — {concern}"
    return alert


# ── Layer 1: Pair relative-shift ─────────────────────────────────────────────

def _layer1(data_a: dict, data_b: dict, sym_a: str, sym_b: str) -> list:
    alerts = []
    q_curr = data_a["quarters"][0] if data_a["quarters"] else ""
    q_prev = data_a["quarters"][1] if len(data_a["quarters"]) > 1 else ""

    for display, stmt, col, threshold, is_abs, base_sev in PAIR_METRICS:
        sa = _s(data_a, stmt, col)
        sb = _s(data_b, stmt, col)

        a0, a1 = _v(sa, 0), _v(sa, 1)
        b0, b1 = _v(sb, 0), _v(sb, 1)

        if any(pd.isna(x) for x in (a0, a1, b0, b1)):
            continue

        if is_abs:
            diff_curr = a0 - b0
            diff_prev = a1 - b1
        else:
            if b0 == 0 or b1 == 0:
                continue
            diff_curr = (a0 - b0) / abs(b0)
            diff_prev = (a1 - b1) / abs(b1)

        shift = diff_curr - diff_prev
        reversal = (diff_curr * diff_prev < 0)

        if not reversal and abs(shift) < threshold:
            continue

        if reversal:
            event_type = "reversal"
            sev = "Critical" if display in ("Revenue", "Net Income", "FCF") else "Warning"
        elif shift > 0:
            event_type = "A gaining"
            sev = base_sev
        else:
            event_type = "A losing"
            sev = base_sev

        # Format differential values for the message
        if is_abs and display in MARGIN_COLS:
            fmt_d = lambda d: f"{d*100:+.0f}pp"
            fmt_shift = f"{shift*100:+.0f}pp"
        elif is_abs:
            fmt_d = lambda d: f"{d:+.2f}x"
            fmt_shift = f"{shift:+.2f}x"
        else:
            fmt_d = lambda d: f"{d*100:+.0f}%"
            fmt_shift = f"{shift*100:+.0f}pp"

        msg = (
            f"{display}: {sym_a} {fmt_d(diff_prev)} vs {sym_b} "
            f"→ {fmt_d(diff_curr)} vs {sym_b} "
            f"[{event_type}, {fmt_shift}]"
        )

        alerts.append(_make(
            "pair",
            metric=display,
            event_type=event_type,
            severity=sev,
            message=msg,
            diff_prior=diff_prev,
            diff_current=diff_curr,
            shift=shift,
            quarter_current=q_curr,
            quarter_prior=q_prev,
        ))

    return alerts


# ── Layer 2a: Individual QoQ / YoY trend ─────────────────────────────────────

def _layer2a(data: dict, symbol: str) -> list:
    alerts = []
    income  = data.get("income",  pd.DataFrame())
    balance = data.get("balance", pd.DataFrame())
    cashflow = data.get("cashflow", pd.DataFrame())
    derived = data.get("derived", pd.DataFrame())
    quarters = data.get("quarters", [])

    def _col(df, col):
        if df.empty or col not in df.columns:
            return pd.Series(dtype=float)
        return pd.to_numeric(df[col], errors="coerce")

    def _append(metric, direction, chg, period, sev, v0, v1, q0, q1, is_margin=False):
        sign = "−" if chg < 0 else "+"
        mag  = f"{abs(chg*100):.0f}pp" if is_margin else f"{abs(chg*100):.0f}%"
        alerts.append(_make(
            "individual_trend",
            stock=symbol,
            metric=metric,
            direction=direction,
            period_type=period,
            severity=sev,
            message=f"{symbol} {metric} {sign}{mag} {period}",
            value_current=v0,
            value_prior=v1,
            change_pct=chg,
            quarter_current=q0,
            quarter_prior=q1,
        ))

    def _check_qoq(df, col, label, drop=None, surge=None, sev_drop="Warning", sev_surge="Info", is_margin=False):
        s = _col(df, col)
        v0, v1 = _v(s, 0), _v(s, 1)
        q0 = quarters[0] if quarters else ""
        q1 = quarters[1] if len(quarters) > 1 else ""
        chg = (v0 - v1) if is_margin else _pct(v0, v1)
        if pd.isna(chg):
            return
        if drop is not None and chg <= drop:
            _append(label, "drop", chg, "QoQ", sev_drop, v0, v1, q0, q1, is_margin)
        if surge is not None and chg >= surge:
            _append(label, "surge", chg, "QoQ", sev_surge, v0, v1, q0, q1, is_margin)

    def _check_yoy(df, col, label, drop, sev="Warning"):
        s = _col(df, col)
        v0, v4 = _v(s, 0), _v(s, 4)
        if pd.isna(v0) or pd.isna(v4):
            return
        chg = _pct(v0, v4)
        if pd.isna(chg) or chg > drop:
            return
        q0 = quarters[0] if quarters else ""
        q4 = quarters[4] if len(quarters) > 4 else ""
        _append(label, "drop", chg, "YoY", sev, v0, v4, q0, q4)

    _check_qoq(income,  "Revenue",         "Revenue",         drop=INDIV_REVENUE_DROP_QOQ, surge=INDIV_REVENUE_SURGE_QOQ, sev_surge="Info")
    _check_qoq(income,  "Net Income",       "Net Income",      drop=-INDIV_NET_INCOME_SWING_QOQ, surge=INDIV_NET_INCOME_SWING_QOQ)
    _check_qoq(derived, "Operating Margin", "Operating Margin",drop=INDIV_OP_MARGIN_DROP_QOQ, is_margin=True)
    _check_qoq(cashflow,"FCF",              "FCF",             drop=INDIV_FCF_DROP_QOQ)
    _check_qoq(balance, "Cash & Equivalents","Cash",           drop=INDIV_CASH_DROP_QOQ)
    _check_qoq(balance, "Total Debt",        "Total Debt",     surge=INDIV_DEBT_SURGE_QOQ)
    _check_qoq(income,  "EPS (Diluted)",     "EPS (Diluted)",  drop=INDIV_EPS_DROP_QOQ)
    _check_yoy(income,  "Revenue",   "Revenue",   INDIV_REVENUE_DROP_YOY)
    _check_yoy(income,  "Net Income","Net Income",INDIV_NET_INCOME_DROP_YOY, sev="Critical")
    _check_yoy(cashflow,"FCF",       "FCF",       INDIV_FCF_DROP_YOY,       sev="Critical")

    return alerts


# ── Layer 2b: Earnings quality / red flags ────────────────────────────────────

def _layer2b(data: dict, symbol: str) -> list:
    alerts = []
    income   = data.get("income",   pd.DataFrame())
    balance  = data.get("balance",  pd.DataFrame())
    cashflow = data.get("cashflow", pd.DataFrame())
    derived  = data.get("derived",  pd.DataFrame())
    quarters = data.get("quarters", [])
    n = len(quarters)

    def _col(df, col):
        if df.empty or col not in df.columns:
            return pd.Series(dtype=float)
        return pd.to_numeric(df[col], errors="coerce")

    def _qa(flag, msg, sev, **kw):
        alerts.append(_make(
            "quality",
            flag=flag,
            stock=symbol,
            severity=sev,
            message=f"{symbol} [{flag}]: {msg}",
            **kw,
        ))

    q0 = quarters[0] if quarters else ""

    rev  = _col(income, "Revenue")
    ni   = _col(income, "Net Income")
    oi   = _col(income, "Operating Income")
    other_inc = _col(income, "Other Income/Expense")
    eps  = _col(income, "EPS (Diluted)")

    td   = _col(balance, "Total Debt")
    cash = _col(balance, "Cash & Equivalents")
    ints = _col(balance, "Intangible Assets")

    ocf    = _col(cashflow, "Operating CF")
    capex  = _col(cashflow, "CapEx")
    fcf    = _col(cashflow, "FCF")
    fin_cf = _col(cashflow, "Financing CF")

    dso      = _col(derived, "DSO")
    inv_days = _col(derived, "Inventory Days")
    ocf_ni   = _col(derived, "OCF/NI")
    accrual  = _col(derived, "Accrual Ratio")

    # ── Check 1: AR outpacing revenue (DSO rising 2+ consecutive quarters) ──
    if len(dso) >= QUALITY_DSO_RISE_QTRS + 1:
        rises = 0
        for i in range(min(QUALITY_DSO_RISE_QTRS, len(dso) - 1)):
            d_now, d_prev = _v(dso, i), _v(dso, i + 1)
            if not pd.isna(d_now) and not pd.isna(d_prev) and d_now > d_prev:
                rises += 1
            else:
                break
        if rises >= QUALITY_DSO_RISE_QTRS:
            d0, d1 = _v(dso, 0), _v(dso, 1)
            if not pd.isna(d0) and not pd.isna(d1):
                _qa("AR outpacing revenue", f"DSO +{d0-d1:.0f}d QoQ ({d1:.0f}→{d0:.0f}d, {rises} qtrs)", "Warning", quarter_current=q0)

    # ── Check 2: Earnings without revenue growth ──
    ni_chg = _pct(_v(ni, 0), _v(ni, 1))
    r_chg  = _pct(_v(rev, 0), _v(rev, 1))
    if not pd.isna(ni_chg) and not pd.isna(r_chg):
        if ni_chg > QUALITY_NI_WITHOUT_REV_NI and r_chg < 0.02:
            _qa("Earnings without revenue growth",
                f"NI +{ni_chg*100:.0f}% QoQ, Revenue +{r_chg*100:.0f}% QoQ", "Warning", quarter_current=q0)

    # ── Check 3: Revenue faster than cash (YoY) ──
    if n > 4:
        r_yoy   = _pct(_v(rev, 0), _v(rev, 4))
        ocf_yoy = _pct(_v(ocf, 0), _v(ocf, 4))
        if not pd.isna(r_yoy) and not pd.isna(ocf_yoy):
            if r_yoy > QUALITY_REV_VS_OCF_REV_YOY and ocf_yoy < 0:
                _qa("Revenue faster than cash",
                    f"Revenue +{r_yoy*100:.0f}% YoY, OCF {ocf_yoy*100:.0f}% YoY", "Warning", quarter_current=q0)

    # ── Check 4: Large opex growth (OpEx proxy = Revenue − Operating Income) ──
    r0, r1  = _v(rev, 0), _v(rev, 1)
    oi0, oi1 = _v(oi, 0), _v(oi, 1)
    if not any(pd.isna(x) for x in (r0, r1, oi0, oi1)) and r1 != 0:
        opex0, opex1 = r0 - oi0, r1 - oi1
        opex_chg = _pct(opex0, opex1)
        r_chg_v  = _pct(r0, r1)
        if not pd.isna(opex_chg) and not pd.isna(r_chg_v):
            gap = opex_chg - r_chg_v
            if gap > QUALITY_OPEX_REV_GAP_QOQ:
                _qa("Large opex growth",
                    f"OpEx +{opex_chg*100:.0f}% QoQ vs Revenue +{r_chg_v*100:.0f}% QoQ (gap +{gap*100:.0f}pp)",
                    "Warning", quarter_current=q0)

    # ── Check 5: Cash declining despite profits (3+ consecutive quarters) ──
    if n >= QUALITY_CASH_DECLINE_QTRS + 1:
        declines = 0
        all_profit = True
        for i in range(QUALITY_CASH_DECLINE_QTRS):
            c0, c1 = _v(cash, i), _v(cash, i + 1)
            ni_q = _v(ni, i)
            if any(pd.isna(x) for x in (c0, c1, ni_q)):
                all_profit = False; break
            if c0 < c1:
                declines += 1
            else:
                break
            if ni_q <= 0:
                all_profit = False
        if declines >= QUALITY_CASH_DECLINE_QTRS and all_profit:
            ni_vals = [_v(ni, i) for i in range(QUALITY_CASH_DECLINE_QTRS) if not pd.isna(_v(ni, i))]
            ni_avg = sum(ni_vals) / len(ni_vals) if ni_vals else float("nan")
            _qa("Cash declining despite profits",
                f"{declines} consecutive qtrs, NI avg ${ni_avg:.1f}B", "Critical", quarter_current=q0)

    # ── Check 6: NI > OCF repeatedly (accrual ratio > threshold 3+ quarters) ──
    if len(accrual) >= QUALITY_NI_OCF_QTRS:
        count = sum(
            1 for i in range(QUALITY_NI_OCF_QTRS)
            if not pd.isna(_v(accrual, i)) and _v(accrual, i) > QUALITY_ACCRUAL_RATIO
        )
        if count >= QUALITY_NI_OCF_QTRS:
            vals = [_v(accrual, i) for i in range(QUALITY_NI_OCF_QTRS) if not pd.isna(_v(accrual, i))]
            avg  = sum(vals) / len(vals) if vals else float("nan")
            _qa("NI > OCF repeatedly",
                f"accrual ratio {avg:.2f} avg ({count} qtrs)", "Critical", quarter_current=q0)

    # ── Check 7: Negative OCF with profits ──
    ocf0, ni0 = _v(ocf, 0), _v(ni, 0)
    if not pd.isna(ocf0) and not pd.isna(ni0) and ocf0 < 0 and ni0 > 0:
        _qa("Negative OCF with profits",
            f"OCF ${ocf0:.1f}B, NI +${ni0:.1f}B ({q0})", "Critical", quarter_current=q0)

    # ── Check 8: Rapid debt growth (>100% YoY = doubling) ──
    if n > 4:
        debt_yoy = _pct(_v(td, 0), _v(td, 4))
        if not pd.isna(debt_yoy) and debt_yoy > QUALITY_DEBT_YOY:
            _qa("Rapid debt growth",
                f"Total Debt +{debt_yoy*100:.0f}% YoY", "Critical", quarter_current=q0)

    # ── Check 9: Intangibles surge (>30% YoY) ──
    if n > 4:
        int_yoy = _pct(_v(ints, 0), _v(ints, 4))
        if not pd.isna(int_yoy) and int_yoy > QUALITY_INTANGIBLES_YOY:
            _qa("Intangibles surge",
                f"+{int_yoy*100:.0f}% YoY", "Warning", quarter_current=q0)

    # ── Check 10: CapEx collapse (>40% drop in spending magnitude QoQ) ──
    cx0, cx1 = _v(capex, 0), _v(capex, 1)
    if not pd.isna(cx0) and not pd.isna(cx1) and cx1 < 0:
        cx_chg = (abs(cx0) - abs(cx1)) / abs(cx1)
        if cx_chg <= QUALITY_CAPEX_COLLAPSE_QOQ:
            _qa("CapEx collapse",
                f"{cx_chg*100:.0f}% QoQ (${abs(cx1):.1f}B → ${abs(cx0):.1f}B)", "Warning", quarter_current=q0)

    # ── Check 11: Recurring non-op gains/losses (same quarter, 2+ years) ──
    if n > 4:
        oi_curr = _v(other_inc, 0)
        oi_prev_yr = _v(other_inc, 4)
        oi0v = _v(oi, 0)
        oi4v = _v(oi, 4)
        if not any(pd.isna(x) for x in (oi_curr, oi_prev_yr, oi0v, oi4v)) and oi0v != 0 and oi4v != 0:
            ratio_curr = abs(oi_curr) / abs(oi0v)
            ratio_prev = abs(oi_prev_yr) / abs(oi4v)
            if ratio_curr > QUALITY_NONOP_INCOME_RATIO and ratio_prev > QUALITY_NONOP_INCOME_RATIO:
                _qa("Recurring non-op gains/losses",
                    f"Other Income ${oi_curr:.1f}B ({ratio_curr*100:.0f}% of OpInc), repeated in same quarter prior year",
                    "Warning", quarter_current=q0)

    # ── Check 12: Inventory buildup (Inventory Days rising 2+ consecutive quarters) ──
    if len(inv_days) >= QUALITY_INVENTORY_RISE_QTRS + 1:
        rises = 0
        for i in range(min(QUALITY_INVENTORY_RISE_QTRS, len(inv_days) - 1)):
            d0, d1 = _v(inv_days, i), _v(inv_days, i + 1)
            if not pd.isna(d0) and not pd.isna(d1) and (d0 - d1) > QUALITY_INVENTORY_DAYS_RISE:
                rises += 1
            else:
                break
        if rises >= QUALITY_INVENTORY_RISE_QTRS:
            id0, id1 = _v(inv_days, 0), _v(inv_days, 1)
            if not pd.isna(id0) and not pd.isna(id1):
                _qa("Inventory buildup",
                    f"Inventory Days +{id0-id1:.0f}d QoQ ({id1:.0f}→{id0:.0f}d, {rises} qtrs)",
                    "Warning", quarter_current=q0)

    # ── Check 13: Low OCF/NI ratio ──
    ratio_val = _v(ocf_ni, 0)
    if not pd.isna(ratio_val) and ratio_val < QUALITY_OCF_NI_RATIO_LOW:
        _qa("Low OCF/NI ratio",
            f"OCF/NI {ratio_val:.2f} (< {QUALITY_OCF_NI_RATIO_LOW})", "Warning", quarter_current=q0)

    # ── Check 14: High accrual ratio ──
    accrual_val = _v(accrual, 0)
    if not pd.isna(accrual_val) and accrual_val > QUALITY_ACCRUAL_RATIO:
        _qa("High accrual ratio",
            f"Accrual Ratio {accrual_val:.3f}", "Warning", quarter_current=q0)

    # ── Check 15: Profits with rising debt ──
    d0, d1 = _v(td, 0), _v(td, 1)
    ni0v = _v(ni, 0)
    if not any(pd.isna(x) for x in (d0, d1, ni0v)):
        debt_qoq = _pct(d0, d1)
        if not pd.isna(debt_qoq) and debt_qoq > QUALITY_DEBT_WITH_PROFITS_QOQ and ni0v > 0:
            _qa("Profits with rising debt",
                f"Debt +{debt_qoq*100:.0f}% QoQ, NI +${ni0v:.1f}B", "Warning", quarter_current=q0)

    # ── Check 16: EPS up, cash flat ──
    eps0, eps1 = _v(eps, 0), _v(eps, 1)
    fcf0, fcf1 = _v(fcf, 0), _v(fcf, 1)
    eps_chg = _pct(eps0, eps1)
    fcf_chg = _pct(fcf0, fcf1)
    if not pd.isna(eps_chg) and not pd.isna(fcf_chg):
        if eps_chg > QUALITY_EPS_CASH_EPS_QOQ and abs(fcf_chg) < QUALITY_EPS_CASH_FCF_QOQ:
            _qa("EPS up, cash flat",
                f"EPS +{eps_chg*100:.0f}% QoQ, FCF {fcf_chg*100:+.0f}% QoQ", "Warning", quarter_current=q0)

    # ── Check 17: External funding dependence ──
    ocf0v = _v(ocf, 0)
    fin0  = _v(fin_cf, 0)
    if not pd.isna(ocf0v) and not pd.isna(fin0) and ocf0v < 0 and fin0 > 0:
        _qa("External funding dependence",
            f"OCF ${ocf0v:.1f}B, Financing CF +${fin0:.1f}B", "Critical", quarter_current=q0)

    return alerts


# ── Main entry point ──────────────────────────────────────────────────────────

def detect_fundamental_alerts(data_a: dict, data_b: dict) -> list:
    """
    Run all alert layers and return a combined list sorted Critical → Warning → Info.

    Parameters
    ----------
    data_a, data_b : output dicts from fetch_fundamentals()
    """
    sym_a = data_a.get("symbol", "A")
    sym_b = data_b.get("symbol", "B")
    alerts = []

    if data_a["n_quarters"] >= 2 and data_b["n_quarters"] >= 2:
        alerts += _layer1(data_a, data_b, sym_a, sym_b)

    for data, sym in [(data_a, sym_a), (data_b, sym_b)]:
        if data["n_quarters"] >= 2:
            alerts += _layer2a(data, sym)
            alerts += _layer2b(data, sym)

    alerts.sort(key=lambda x: _SEV_ORDER.get(x.get("severity", "Info"), 3))
    return alerts
