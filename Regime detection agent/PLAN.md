# Regime Detection Agent — Plan

## Overview

This agent detects macro regime changes by monitoring five market indicators using
free data sources (yfinance + FRED). It runs entirely in Streamlit — no DB writes,
no scheduled jobs — and surfaces triggered alerts in the **Regime Alerts** tab alongside
the existing AI correlation commentary.

---

## Files

| File | Purpose |
|---|---|
| `data_collector.py` | Fetches all 5 indicators; entry point `fetch_indicators(lookback_days)` |
| `regime_alerts.py` | Evaluates alert rules against indicator data; entry point `detect_alerts(df)` |
| `PLAN.md` | This document |

`app/streamlit_app.py` imports both modules and renders results in the Regime Alerts tab.

---

## Indicators & Data Sources

| # | Indicator | Column | Source | Ticker / Series |
|---|---|---|---|---|
| 1 | 10Y Treasury yield | `treasury_10y` | yfinance | `^TNX` |
| 2 | 10Y TIPS real yield | `tips_10y` | FRED API | `DFII10` |
| 3 | Nasdaq-100 breadth | `nasdaq_breadth` | yfinance (computed) | ~98 NDX-100 components |
| 4 | VIX | `vix` | yfinance | `^VIX` |
| 5 | SMH/QQQ ratio | `smh_qqq_ratio` | yfinance | `SMH`, `QQQ` |

**Derived columns also returned:**
- `smh_qqq_zscore` — ratio z-score vs trailing 252-day window

**Env vars required:**
- `FRED_API_KEY` — free key from fred.stlouisfed.org (for TIPS yield)

**Python deps added:** `fredapi`

---

## Alert Rules

### 1. 10Y Treasury Yield

| Rule | Condition | Severity |
|---|---|---|
| Yield above 50DMA | `treasury_10y > rolling_50d_mean` | Warning |
| 50DMA > 200DMA (golden cross — bearish for bonds) | `MA50 > MA200` | Warning |
| 20-day rate of change > 50 bps | `yield[today] − yield[−20d] > 0.50%` | Warning |

**Rationale:** Rising nominal yields signal tighter financial conditions. A yield
above its 50DMA indicates short-term uptrend; 50>200 confirms a structural uptrend;
20-day ROC > 50 bps flags an acceleration that tends to stress equity valuations.

---

### 2. Real Yields (10Y TIPS)

| Rule | Condition | Severity |
|---|---|---|
| Real yield above 1% | `tips_10y > 1.00` | Warning |
| 20DMA > 100DMA | `MA20 > MA100` | Warning |
| Rose > 50 bps in 20 trading days | `tips[today] − tips[−20d] > 0.50%` | Warning |

**Rationale:** Real yields above 1% historically compress equity multiples, especially
for long-duration growth stocks. 20DMA > 100DMA indicates sustained real-rate pressure.
A 50 bps monthly surge often precedes sector rotation out of tech.

---

### 3. Nasdaq-100 Breadth (% above 200DMA)

| Zone | Condition | Severity | Interpretation |
|---|---|---|---|
| Strong | `breadth ≥ 70%` | Info | Broad participation, risk-on |
| Neutral | `50% ≤ breadth < 70%` | Info | Mixed, monitor |
| Warning | `30% ≤ breadth < 50%` | Warning | Narrowing, caution |
| Severe risk-off | `breadth < 30%` | Critical | Broad deterioration |

**Crossing alerts also fire when the reading crosses below 50% or 30% within 5 days.**

**Rationale:** Breadth measures the health of the rally beneath the surface. A small
number of mega-caps can hold up the index while most stocks are breaking down — breadth
exposes this divergence.

---

### 4. VIX Trend

| Rule | Condition | Severity |
|---|---|---|
| Rising volatility regime: 20DMA > 100DMA | `VIX_MA20 > VIX_MA100` | Warning |

**Display metric:** 20DMA / 100DMA ratio (>1.0 = rising vol regime).

**Rationale:** Spot VIX is noisy. Comparing the 20DMA to the 100DMA filters out
short-lived spikes and identifies sustained elevated-volatility regimes that
typically accompany risk-off positioning.

---

### 5. SMH/QQQ Relative Strength

| Rule | Condition | Severity |
|---|---|---|
| Ratio below 100DMA (semis lagging tech broadly) | `ratio < MA100` | Warning |
| 50DMA crosses below 200DMA (death cross) | `MA50 < MA200` | Critical |

**Rationale:** Semiconductors (SMH) historically lead the broader tech sector (QQQ)
in both directions. Semis lagging below their 100DMA signals softening forward
momentum. A 50/200 death cross on the relative ratio indicates a structural shift
in leadership — historically a precursor to broader tech underperformance.

---

## Thresholds (all in `regime_alerts.py`)

```python
TREASURY_ROC_THRESHOLD = 0.50   # 50 bps 20-day change → alert
TIPS_LEVEL_THRESHOLD   = 1.00   # real yield level → alert
TIPS_MONTHLY_RISE      = 0.50   # 50 bps 20-day rise → alert
CROSS_LOOKBACK         = 5      # trading days to look back for "recently crossed"
```

---

## Architecture Decisions

- **No DB writes** — regime indicators are market data, not application state. Fetched
  fresh on every Streamlit session; cached in-process for 1 hour (`ttl=3600`).
- **yfinance + FRED only** — no additional API accounts or paid data. FRED publishes
  TIPS yields with a 1-day lag; NaN on the most recent date is expected and handled.
- **Calendar filter (`close[QQQ.notna()]`)** — `^TNX` and `^VIX` trade on CBOE
  holidays when equities are closed. Without this filter, the extra rows inject NaN
  into the NDX-100 close data and break the 200DMA calculation. QQQ is used as the
  stock-market calendar reference.
- **DMA_BUFFER = 400 calendar days** — the 252-day SMH/QQQ z-score window requires
  252 trading days of warmup before the first valid value. 400 calendar days ≈ 276
  trading days, providing sufficient headroom for both the 200DMA and z-score to be
  valid across the entire 1-year display window.
- **State-based + recent-crossing alerts** — each rule reports both the current state
  (`triggered: bool`) and whether the state changed within the last 5 trading days
  (`recently_crossed: bool`). This distinguishes a new crossing (actionable) from a
  condition that has been true for weeks (already priced in).
- **Soft failures** — if the FRED API is unreachable, or fewer than the required rows
  exist for a given MA, that specific rule is skipped silently. Other indicators are
  unaffected.

---

## Data Flow

```
Streamlit tab_alerts
  │
  ├─ _regime_indicators()  [cached 1h]
  │     └─ fetch_indicators(lookback_days=365)
  │           ├─ yf.download(^TNX, ^VIX, SMH, QQQ, ~98 NDX tickers)
  │           ├─ filter to stock trading days (QQQ non-NaN)
  │           ├─ compute breadth, SMH/QQQ ratio + z-score
  │           └─ FRED.get_series("DFII10")
  │
  └─ detect_alerts(df)
        ├─ 3 Treasury rules
        ├─ 3 TIPS rules
        ├─ 2 Breadth rules (zone + crossing)
        ├─ 1 VIX rule
        └─ 2 SMH/QQQ rules
```
