# Fundamental Comparison Agent — Plan

## Overview

A new Streamlit tab ("Fundamental Comparison") and supporting agent module that lets
the user pick any two stocks from the database, fetches their quarterly financials
via yfinance, runs two layers of alerts (pair vs pair, and each stock vs its own
history), then calls Claude to write a ~200-word plain-English fundamental briefing.

---

## Folder Structure

```
fundamental comparison agent/
├── PLAN.md                        ← this document
├── fundamental_data.py            ← fetches & normalizes yfinance quarterly statements
├── fundamental_alerts.py          ← alert engine (pair + individual QoQ/YoY)
└── fundamental_commentary.py      ← Claude API agent for written briefing
```

`app/streamlit_app.py` will import the three modules (via `sys.path.insert`) and
render a new 7th tab between "Regime Alerts" and "Manage Tickers".

---

## Data Sources (yfinance)

All data fetched via `yfinance.Ticker(symbol)` — no additional API keys required.

| Statement | yfinance attribute | Columns |
|---|---|---|
| Income Statement | `.quarterly_income_stmt` | Last 4 reported quarters (most-recent first) |
| Balance Sheet | `.quarterly_balance_sheet` | Last 4 reported quarters |
| Cash Flow | `.quarterly_cashflow` | Last 4 reported quarters |

yfinance returns DataFrames where:
- **columns** = quarter-end dates (descending, so `col[0]` = most recent quarter)
- **index** = metric names (strings like `"Total Revenue"`, `"Net Income"`)

---

## Metrics Tracked

### Income Statement

| yfinance row name | Display name | Notes |
|---|---|---|
| `Total Revenue` | Revenue | Core top-line |
| `Cost Of Revenue` | COGS | Needed for Inventory Days ratio |
| `Gross Profit` | Gross Profit | |
| `Operating Income` | Operating Income | |
| `Net Income` | Net Income | |
| `EBITDA` | EBITDA | |
| `Research And Development` | R&D Expense | May be NaN for some tickers |
| `Basic EPS` | EPS (Basic) | Per-share earnings |
| `Diluted EPS` | EPS (Diluted) | |

**Derived:** Gross Margin = Gross Profit / Revenue; Operating Margin = Operating
Income / Revenue; Net Margin = Net Income / Revenue

### Balance Sheet

| yfinance row name | Display name | Notes |
|---|---|---|
| `Total Assets` | Total Assets | |
| `Total Liabilities Net Minority Interest` | Total Liabilities | |
| `Stockholders Equity` | Shareholders' Equity | |
| `Cash And Cash Equivalents` | Cash & Equivalents | |
| `Total Debt` | Total Debt | Short + long term |
| `Current Assets` | Current Assets | |
| `Current Liabilities` | Current Liabilities | |
| `Accounts Receivable` | Accounts Receivable | Needed for DSO; NaN for some tickers |
| `Inventory` | Inventory | Needed for Inventory Days; NaN for asset-light firms |
| `Goodwill And Other Intangible Assets` | Intangible Assets | For intangibles growth check |

**Derived:** D/E Ratio = Total Debt / Stockholders Equity; Current Ratio =
Current Assets / Current Liabilities

### Cash Flow

| yfinance row name | Display name | Notes |
|---|---|---|
| `Operating Cash Flow` | Operating CF | |
| `Capital Expenditure` | CapEx | yfinance stores as negative number |
| `Free Cash Flow` | FCF | Pulled directly if present; else Operating CF + CapEx |
| `Financing Cash Flow` | Financing CF | Needed for external-funding dependence check |

### Derived Quality Ratios (computed in `fundamental_data.py`)

These are calculated per quarter from the raw statements and stored in the
`"derived"` DataFrame alongside margins and D/E. They feed Layer 2b alert checks.

| Ratio | Formula | Interpretation |
|---|---|---|
| DSO | `Accounts Receivable / (Revenue / 90)` | Days to collect cash; rising = customers paying slower |
| Inventory Days | `Inventory / (COGS / 90)` | Days to sell inventory; NaN if no inventory |
| Current Ratio | `Current Assets / Current Liabilities` | Short-term liquidity |
| D/E Ratio | `Total Debt / Shareholders' Equity` | Leverage |
| OCF/NI | `Operating Cash Flow / Net Income` | <1 consistently = earnings not backed by cash |
| FCF Margin | `FCF / Revenue` | Cash generation efficiency |
| Accrual Ratio | `(Net Income − Operating Cash Flow) / Total Assets` | >0.10 = earnings quality concern |

---

## Two-Layer Alert System

### Layer 1 — Pair Relative-Shift Alerts (how A-vs-B changed quarter over quarter)

This layer is **not** a snapshot comparison of A vs B in the current quarter.
It tracks how the **relative gap between A and B shifted** from the prior quarter
to the current quarter. The alert fires when the differential moved materially —
either a reversal (who was ahead flipped) or a significant widening/narrowing of
the gap.

**Mechanics:**
For each metric, compute the relative differential for both quarters:

```
diff_q  = (metric_A_current  − metric_B_current)  / |metric_B_current|
diff_q1 = (metric_A_prior    − metric_B_prior)    / |metric_B_prior|
shift   = diff_q − diff_q1
```

Three alert sub-types fire based on `diff_q`, `diff_q1`, and `shift`:

| Sub-type | Condition | Example |
|---|---|---|
| **Reversal** | `sign(diff_q) ≠ sign(diff_q1)` — who is ahead flipped | A had +30% more net income than B last quarter; now A has −30% less |
| **Gap widening** | `shift > threshold` and same sign (A pulling further ahead) | A's revenue lead over B grew from 5% to 35% |
| **Gap narrowing** | `shift < −threshold` and same sign (B catching up or A losing lead) | A's FCF advantage over B shrank from 40% to 5% |

Reversals always fire regardless of magnitude (a sign flip is inherently
significant). Widening/narrowing fire only when `|shift|` exceeds the per-metric
threshold.

| Metric | Widening/narrowing threshold | Severity |
|---|---|---|
| Revenue | 15 pp shift in differential | Warning |
| Net Income | 20 pp shift | Warning |
| Gross Margin | 8 pp shift | Warning |
| Operating Margin | 8 pp shift | Warning |
| Net Margin | 8 pp shift | Warning |
| FCF | 20 pp shift | Warning |
| D/E Ratio | 30 pp shift in ratio differential | Warning |
| Current Ratio | 0.5 absolute shift in ratio differential | Warning |
| R&D % of Revenue | 10 pp shift | Info |

Reversals on Revenue, Net Income, or FCF are escalated to **Critical**.

**Layer 1 message format — concise, one line:**
```
"{Metric}: {A} {prior_rel} vs {B} → {curr_rel} [{event_type}, {shift:+.0f}pp]"

Examples:
  "Net Income: ARM +28% vs TSM → −15% vs TSM [reversal, −43pp]"
  "FCF: NVDA +12% vs ARM → +47% vs ARM [widening, +35pp]"
  "Operating Margin: TSM +3% vs ARM → +14% vs ARM [widening, +11pp]"
```

### Layer 2a — Individual Trend Alerts (QoQ / YoY)

Runs independently for each stock. Compares most-recent quarter vs prior quarter
(QoQ) and vs same quarter one year ago (YoY). Requires 4 quarters of data.

| Metric | Condition | Severity |
|---|---|---|
| Revenue | QoQ decline > 10% | Warning |
| Revenue | QoQ growth > 30% | Info |
| Net Income | QoQ swing > ±20% | Warning |
| Operating Margin | QoQ contraction > 5 pp | Warning |
| FCF | QoQ decline > 25% | Warning |
| Cash & Equivalents | QoQ decline > 20% | Warning |
| Total Debt | QoQ surge > 25% | Warning |
| EPS (Diluted) | QoQ decline > 15% | Warning |
| Revenue | YoY decline > 5% | Warning |
| Net Income | YoY decline > 25% | Critical |
| FCF | YoY decline > 30% | Critical |

**Layer 2a message format — concise, one line:**
```
"{Stock} {Metric} {direction}{magnitude} {period}"

Examples:
  "ARM FCF −31% QoQ"
  "TSM Revenue −8% YoY"
  "NVDA Net Income +22% QoQ"
  "ARM Operating Margin −6pp QoQ"
```

### Layer 2b — Earnings Quality & Red Flag Screening

Pattern-based checks that require 3–4 quarters of history. Detects signs of
deteriorating earnings quality, accounting anomalies, or structural financial
stress. Each check uses the derived quality ratios.

All checks run per stock, independently. Pattern checks (marked **P**) look across
all available quarters (up to 4); single-quarter checks (marked **S**) compare only
the most recent quarter.

| # | Flag | Detection logic | Type | Severity |
|---|---|---|---|---|
| 1 | **AR outpacing revenue** | DSO rising QoQ for 2+ consecutive quarters AND revenue not growing faster than AR | P | Warning |
| 2 | **Earnings without revenue growth** | Net Income QoQ > 10% AND Revenue QoQ < 2% (same quarter) | S | Warning |
| 3 | **Revenue faster than cash** | Revenue YoY > 10% AND Operating CF YoY < 0 (cash actually declining) | S | Warning |
| 4 | **Large opex growth** | Total operating expenses growing > 15pp faster than Revenue QoQ | S | Warning |
| 5 | **Cash declining despite profits** | Cash QoQ negative for 3+ consecutive quarters AND Net Income > 0 in each | P | Critical |
| 6 | **NI > OCF repeatedly** | Accrual Ratio > 0.10 for 3+ consecutive quarters (Net Income consistently exceeds OCF) | P | Critical |
| 7 | **Negative OCF with profits** | OCF < 0 AND Net Income > 0 in the same quarter | S | Critical |
| 8 | **Rapid debt growth** | Total Debt YoY > 100% (doubling in one year) | S | Critical |
| 9 | **Intangibles surge** | Intangible Assets YoY > 30% (large acquisition or capitalized R&D spike) | S | Warning |
| 10 | **CapEx collapse** | CapEx QoQ decline > 40% (sudden halt in investment) | S | Warning |
| 11 | **Recurring non-op gains/losses** | `Other Income Expense` > 10% of Operating Income in same quarter for 2+ years (non-recurring items repeating) | P | Warning |
| 12 | **Inventory buildup** | Inventory Days QoQ rising > 15 days for 2+ consecutive quarters (demand weakness signal) | P | Warning |
| 13 | **Low OCF/NI ratio** | OCF/NI < 0.75 in the current quarter (earnings not well-backed by cash) | S | Warning |
| 14 | **High accrual ratio** | Accrual Ratio > 0.10 in the current quarter | S | Warning |
| 15 | **Profits with rising debt** | Net Income QoQ > 0 AND Total Debt QoQ > 15% — profits not generating cash to self-fund | S | Warning |
| 16 | **EPS up, cash flat** | EPS (Diluted) QoQ > 10% AND FCF QoQ change < 5% (financial engineering signal — buybacks or accounting, not real cash growth) | S | Warning |
| 17 | **External funding dependence** | OCF < 0 AND Financing CF > 0 in the same quarter — company is burning cash and plugging the gap with debt/equity issuance | S | Critical |

#### Pattern → Potential Concern Reference

When any of the following co-occurrence patterns is detected, the alert message
includes the "Potential Concern" label so the reader immediately understands the
risk implication. The labels are used verbatim in the alert and passed to Claude
for commentary framing.

| Abnormality pattern | Potential Concern | Maps to check(s) |
|---|---|---|
| Revenue ↑, Receivables ↑↑ | Aggressive revenue recognition | #1 AR outpacing revenue |
| Sales flat, Inventory ↑↑ | Weak demand | #12 Inventory buildup |
| Net Income ↑, Operating Cash Flow ↓ | Low earnings quality | #6 NI>OCF repeatedly, #13 Low OCF/NI ratio |
| Profits ↑, Debt ↑ | Profit not translating into cash | #15 Profits with rising debt |
| Acquisitions ↑, Goodwill ↑↑ | Overpayment risk | #9 Intangibles surge |
| EPS ↑, Cash Flow flat | Financial engineering | #16 EPS up, cash flat |
| Operating CF negative, Financing CF positive | Dependence on external funding | #17 External funding dependence |

Detection requires both legs of each pattern to be present in the same quarter.
The "Potential Concern" label is appended to the alert message as a second clause.

**Layer 2b message format — concise, one line; concern label as second clause:**
```
"{Stock} [{Flag name}]: {key figures} — {Potential Concern}"

Examples:
  "ARM [AR outpacing revenue]: DSO +8d QoQ (42→50d, 2 qtrs) — Aggressive revenue recognition"
  "NVDA [NI>OCF repeatedly]: accrual ratio 0.12 avg (3 qtrs) — Low earnings quality"
  "TSM [Rapid debt growth]: Total Debt +218% YoY"
  "ARM [Intangibles surge]: +34% YoY — Overpayment risk"
  "NVDA [CapEx collapse]: −52% QoQ"
  "TSM [Cash declining despite profits]: 3 qtrs, NI avg $2.1B"
  "ARM [Negative OCF with profits]: OCF −$0.4B, NI +$0.8B (Q3 2025) — Low earnings quality"
  "NVDA [Earnings without revenue growth]: NI +18% QoQ, Revenue +1% QoQ"
  "TSM [Profits with rising debt]: Debt +22% QoQ, NI +$1.2B — Profit not translating into cash"
  "ARM [EPS up, cash flat]: EPS +14% QoQ, FCF −1% QoQ — Financial engineering"
  "NVDA [External funding dependence]: OCF −$1.1B, Financing CF +$2.3B — Dependence on external funding"
```

Checks without a matching pattern row (#2–5, #7–8, #10–11, #14) display without
the concern clause — the flag name is self-explanatory.

All thresholds for both 2a and 2b defined as named constants at the top of
`fundamental_alerts.py` so they can be tuned without touching logic.

---

## Alert Output Schema

Each alert is a dict:

```python
# Layer 1 — pair relative-shift alert
{
    "layer": "pair",
    "metric": "Net Income",
    "event_type": "reversal" | "widening" | "narrowing",
    "severity": "Info" | "Warning" | "Critical",
    "message": "Net Income: ARM +28% vs TSM → −15% vs TSM [reversal, −43pp]",
    "diff_prior": 0.28,       # (A − B) / |B| prior quarter
    "diff_current": -0.15,    # (A − B) / |B| current quarter
    "shift": -0.43,           # diff_current − diff_prior
    "quarter_current": "Q3 2025",
    "quarter_prior":   "Q2 2025",
}

# Layer 2a — individual QoQ/YoY trend alert
{
    "layer": "individual_trend",
    "stock": "ARM",
    "metric": "FCF",
    "direction": "drop" | "surge",
    "period_type": "QoQ" | "YoY",
    "severity": "Info" | "Warning" | "Critical",
    "message": "ARM FCF −31% QoQ",
    "value_current": 1.2e9,
    "value_prior": 1.74e9,
    "change_pct": -0.31,
    "quarter_current": "Q3 2025",
    "quarter_prior":   "Q2 2025",
}

# Layer 2b — earnings quality / red flag alert
{
    "layer": "quality",
    "stock": "TSM",
    "flag": "Profits with rising debt",  # matches flag name from table above
    "concern": "Profit not translating into cash",  # None if no pattern match
    "check_type": "S" | "P",             # single-quarter or pattern
    "severity": "Info" | "Warning" | "Critical",
    "message": "TSM [Profits with rising debt]: Debt +22% QoQ, NI +$1.2B — Profit not translating into cash",
    "quarters_triggered": 1,             # for pattern checks: how many qtrs matched
    "key_ratio": "Total Debt QoQ",
    "key_value": 0.22,                   # the ratio/change value that triggered
    "quarter_current": "Q3 2025",
}
```

---

## `fundamental_data.py`

Entry point: `fetch_fundamentals(symbol: str) -> dict`

Returns:
```python
{
    "income":    pd.DataFrame,   # 4 quarters × selected metrics, index = display name
    "balance":   pd.DataFrame,
    "cashflow":  pd.DataFrame,
    "derived":   pd.DataFrame,   # margins, ratios, FCF — same shape
    "quarters":  list[str],      # e.g. ["Q3 2025", "Q2 2025", "Q1 2025", "Q4 2024"]
    "currency":  str,            # "USD" or "TWD" — flagged from ticker info
    "symbol":    str,
}
```

**Normalization steps:**
1. Fetch up to **8 quarters** from yfinance (needed for YoY comparisons in quality
   checks and for the "same quarter prior year" non-op income repeat detection).
   Display tables show only the most recent 4; alert engine uses all available.
2. Sort descending (most-recent first) so `iloc[0]` = current quarter, `iloc[1]` =
   prior quarter, `iloc[4]` = same quarter one year ago.
3. Convert raw numbers to billions for display (divide by 1e9) where appropriate;
   EPS, margins, ratios, and DSO stay at their natural scale.
4. Rename yfinance row names → display names via a mapping dict.
5. Compute derived metrics and quality ratios after normalization (see table above).
6. Handle missing rows gracefully — if a metric is not present for a given ticker,
   that column is NaN in the DataFrame (no crash). Quality checks skip silently
   when their required inputs are NaN.

**QoQ comparison convention (confirmed):** The display tables show the most recent
quarter (e.g., Q1 2026) with a ▲/▼ delta arrow representing the change vs the
immediately prior quarter (Q4 2025). This flows directly from `iloc[0]` vs `iloc[1]`
in the sorted DataFrame. The same indexing applies to all alert checks: every "QoQ"
alert compares the current quarter to the one immediately before it.

**Caveat flagged in UI:** TSM reports in TWD; all figures for TSM are in TWD billions
unless yfinance provides a USD conversion (it does not). A banner will note this.

---

## `fundamental_alerts.py`

Entry point: `detect_fundamental_alerts(data_a: dict, data_b: dict) -> list[dict]`

Internal flow:
1. Extract current and prior quarter rows for both stocks (needed for Layer 1 shift).
2. **Layer 1**: compute per-metric differential for both quarters → classify each as
   reversal / widening / narrowing → append triggered alerts.
3. **Layer 2a**: for each stock, compute QoQ and YoY changes on trend metrics →
   append triggered alerts.
4. **Layer 2b**: for each stock, run all 14 quality/red-flag checks using the
   `"derived"` DataFrame (which spans all 4 quarters):
   - Single-quarter checks (S): use current quarter row only.
   - Pattern checks (P): iterate across all available quarters to count consecutive
     or repeated conditions; fire when the count meets the threshold.
5. Return combined list sorted by severity (Critical first); caller can filter
   by `layer` or `stock`.

---

## `fundamental_commentary.py`

Entry point: `generate_fundamental_commentary(data_a, data_b, alerts) -> str`

Called on-demand (button click in Streamlit). No DB writes — returns the text
string directly to the caller for display in the UI.

### Anti-hallucination constraint (hardcoded)

Claude is given a `system` prompt that strictly prohibits it from using any
information outside what is supplied in the `KEY METRICS` and `ALERTS` blocks:

```
You are a financial analyst assistant.
Your analysis must be grounded exclusively in the data supplied in the user message.
You have no access to the internet, live prices, news, earnings calls, analyst
reports, or any information beyond what is explicitly listed in KEY METRICS and ALERTS.
Do not invent, estimate, or infer any figure that is not present in the supplied data.
If a metric is N/A or missing, omit it rather than guessing.
Do not reference any external events, product launches, management commentary,
macro conditions, or market context that is not derived directly from the numbers
provided.
```

The user prompt also includes the explicit rule: *"Base every statement exclusively
on the KEY METRICS and ALERTS above — no external data."* Both the system and user
prompt carry the constraint so the instruction is not discardable by a rogue turn.

### Prompt structure sent to Claude:
- KEY METRICS block: Revenue, Net Income, FCF, Margins, D/E, OCF/NI, Accrual Ratio
  for both stocks — current quarter values + QoQ delta (all sourced from yfinance)
- ALERTS block: triggered Layer 1, 2a, and 2b alerts grouped by type and stock
- Coverage instructions: (a) which stock has stronger fundamentals, (b) significant
  pair shifts, (c) individual trend changes, (d) quality red flags
- Style rules: Critical alerts first; use ticker names; note non-USD currency;
  no boilerplate openings; omit N/A metrics

Model: `claude-sonnet-4-6`, `max_tokens=400`

---

## Streamlit Tab Layout

New tab added between "Regime Alerts" and "Manage Tickers": **"Fundamental Comparison"**

```
┌──────────────────────────────────────────────────────────────────────┐
│  Stock A  [dropdown ▾]     Stock B  [dropdown ▾]   [Load]           │
├──────────────────────────────────────────────────────────────────────┤
│  Sub-tabs:  Income Statement | Balance Sheet | Cash Flow             │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Side-by-side metric table: Stock A vs Stock B                 │  │
│  │  Columns: Metric | A (Q) | B (Q) | Δ A QoQ | Δ B QoQ         │  │
│  │  Bar charts: Revenue, Net Income, FCF over last 4 quarters     │  │
│  └────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────┤
│  Alert Panel                                                         │
│  Left: Pair alerts (A vs B)     Right: Individual alerts             │
│  Severity badges: 🔴 Critical  🟡 Warning  🔵 Info                   │
├──────────────────────────────────────────────────────────────────────┤
│  [Generate AI Commentary]                                            │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Claude's ~200-word fundamental comparison briefing            │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**Caching:** `@st.cache_data(ttl=21600)` (6 hours) on `fetch_fundamentals()` —
fundamentals only update when earnings are reported (quarterly), so 6 hours is
generous and avoids hammering yfinance.

**Stock picker default:** ARM (Stock A) and TSM (Stock B) — the project's primary pair.

---

## Integration Points

- `sys.path.insert(0, ...)` for `"fundamental comparison agent"` added at the top
  of `app/streamlit_app.py` (alongside existing path inserts for other agents).
- Tab is added to the `st.tabs([...])` call — 7th tab, between "Regime Alerts"
  and "Manage Tickers".
- No new DB tables required — commentary is displayed in-session only (no
  persistence). If persistence is desired later, a `fundamental_alerts` table
  can be added to `db/schema.sql`.
- No new Python dependencies — yfinance and anthropic are already installed.

---

## Thresholds Reference (all in `fundamental_alerts.py`)

```python
# ── Layer 1: Pair relative-shift ─────────────────────────────────────────────
# Reversals always fire; these govern widening/narrowing alerts only
PAIR_REVENUE_SHIFT         = 0.15   # 15 pp shift in revenue differential
PAIR_NET_INCOME_SHIFT      = 0.20   # 20 pp shift in net income differential
PAIR_GROSS_MARGIN_SHIFT    = 0.08   # 8 pp shift in gross margin differential
PAIR_OP_MARGIN_SHIFT       = 0.08   # 8 pp shift in operating margin differential
PAIR_NET_MARGIN_SHIFT      = 0.08   # 8 pp shift in net margin differential
PAIR_FCF_SHIFT             = 0.20   # 20 pp shift in FCF differential
PAIR_DE_RATIO_SHIFT        = 0.30   # 30 pp shift in D/E ratio differential
PAIR_CURRENT_RATIO_SHIFT   = 0.50   # 0.5 absolute shift in current ratio differential
PAIR_RD_RATIO_SHIFT        = 0.10   # 10 pp shift in R&D-as-%-of-revenue differential

# ── Layer 2a: Individual QoQ ──────────────────────────────────────────────────
INDIV_REVENUE_DROP_QOQ     = -0.10  # -10% revenue decline
INDIV_REVENUE_SURGE_QOQ    =  0.30  # +30% positive surprise
INDIV_NET_INCOME_SWING_QOQ =  0.20  # ±20% swing
INDIV_OP_MARGIN_DROP_QOQ   = -0.05  # -5 pp contraction
INDIV_FCF_DROP_QOQ         = -0.25  # -25% FCF decline
INDIV_CASH_DROP_QOQ        = -0.20  # -20% cash decline
INDIV_DEBT_SURGE_QOQ       =  0.25  # +25% debt surge
INDIV_EPS_DROP_QOQ         = -0.15  # -15% EPS decline

# ── Layer 2a: Individual YoY ──────────────────────────────────────────────────
INDIV_REVENUE_DROP_YOY     = -0.05  # -5% YoY revenue decline
INDIV_NET_INCOME_DROP_YOY  = -0.25  # -25% YoY net income decline
INDIV_FCF_DROP_YOY         = -0.30  # -30% YoY FCF decline

# ── Layer 2b: Earnings quality / red flags ────────────────────────────────────
QUALITY_DSO_RISE_DAYS      =  0     # any consecutive QoQ rise counts; flag after 2 qtrs
QUALITY_NI_WITHOUT_REV_NI  =  0.10  # NI QoQ > 10% with Revenue QoQ < 2%
QUALITY_REV_VS_OCF_REV_YOY =  0.10  # Revenue YoY > 10% while OCF YoY < 0
QUALITY_OPEX_REV_GAP_QOQ   =  0.15  # OpEx growth > Revenue growth by 15 pp
QUALITY_CASH_DECLINE_QTRS  =  3     # cash falling for this many consecutive quarters
QUALITY_NI_OCF_QTRS        =  3     # accrual ratio > threshold for this many qtrs
QUALITY_ACCRUAL_RATIO       =  0.10  # threshold for accrual ratio (single-qtr check too)
QUALITY_OCF_NI_RATIO_LOW    =  0.75  # OCF/NI below this = earnings quality concern
QUALITY_DEBT_YOY            =  1.00  # Total Debt YoY > 100% (doubling)
QUALITY_INTANGIBLES_YOY     =  0.30  # Intangibles YoY > 30%
QUALITY_CAPEX_COLLAPSE_QOQ  = -0.40  # CapEx QoQ decline > 40%
QUALITY_NONOP_INCOME_RATIO  =  0.10  # |Other Income| > 10% of Operating Income
QUALITY_NONOP_REPEAT_YEARS  =  2     # same-quarter non-op income flagged in 2+ years
QUALITY_INVENTORY_DAYS_RISE =  15    # Inventory Days rising > 15 days QoQ; flag after 2

# ── Layer 2b: Pattern → concern checks (checks 15–17) ─────────────────────────
QUALITY_DEBT_WITH_PROFITS_QOQ =  0.15  # Debt QoQ > 15% while NI > 0
QUALITY_EPS_CASH_EPS_QOQ      =  0.10  # EPS QoQ > 10% threshold
QUALITY_EPS_CASH_FCF_QOQ      =  0.05  # FCF QoQ < 5% (flat) for EPS/cash divergence
# Check 17 (External funding dependence) has no threshold — fires whenever OCF<0 AND Financing CF>0
```

---

## Architecture Decisions

- **No DB writes** — fundamental data is live from yfinance and stale within hours
  of an earnings release; caching in Streamlit session state is sufficient. No new
  schema changes required.
- **In-memory only for commentary** — consistent with the existing commentary.py
  approach in the regime agent (on-demand, not ETL-triggered).
- **yfinance quarterly attributes** — `.quarterly_income_stmt` etc. are used rather
  than annual because the user explicitly wants quarter-level comparisons and alerts.
- **4-quarter window** — limiting display to 4 quarters keeps the UI readable and
  ensures QoQ and YoY deltas are both computable from the same fetch.
- **Soft failure on missing metrics** — if a ticker (e.g., ARM) does not report a
  given line item (e.g., EBITDA), that metric shows as "N/A" in the table and is
  silently skipped in alert evaluation. No crashes.
- **TSM currency caveat** — TSM (TSMC) files in TWD. yfinance does not auto-convert
  to USD. The UI will display a prominent banner when TSM is selected so the user
  doesn't compare TWD figures to USD figures as if they were the same unit.
- **Default pair ARM/TSM** — mirrors the default across all other tabs for
  consistency.

---

## Data Flow

```
Streamlit tab_fundamentals
  │
  ├─ fetch_fundamentals("ARM")   [cached 6h]
  │     └─ yf.Ticker("ARM")
  │           ├─ .quarterly_income_stmt
  │           ├─ .quarterly_balance_sheet
  │           └─ .quarterly_cashflow
  │
  ├─ fetch_fundamentals("TSM")   [cached 6h]
  │
  ├─ detect_fundamental_alerts(data_arm, data_tsm)
  │     ├─ Layer 1 pair relative-shift  (9 metrics × reversal/widen/narrow)
  │     ├─ Layer 2a trend ARM           (11 QoQ/YoY checks)
  │     ├─ Layer 2a trend TSM           (11 QoQ/YoY checks)
  │     ├─ Layer 2b quality ARM         (17 red-flag checks, S + P)
  │     └─ Layer 2b quality TSM         (17 red-flag checks, S + P)
  │
  └─ [button] generate_fundamental_commentary(data_arm, data_tsm, alerts)
        └─ Claude claude-sonnet-4-6 → ~200-word briefing
```
