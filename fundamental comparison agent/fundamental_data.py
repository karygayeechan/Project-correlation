"""
Fetches and normalizes quarterly financial statements for a given ticker via yfinance.
Entry point: fetch_fundamentals(symbol: str) -> dict
"""
import numpy as np
import pandas as pd
import yfinance as yf

# ── Metric rename maps ────────────────────────────────────────────────────────
INCOME_MAP = {
    "Total Revenue": "Revenue",
    "Cost Of Revenue": "COGS",
    "Gross Profit": "Gross Profit",
    "Operating Income": "Operating Income",
    "Net Income": "Net Income",
    "EBITDA": "EBITDA",
    "Research And Development": "R&D Expense",
    "Basic EPS": "EPS (Basic)",
    "Diluted EPS": "EPS (Diluted)",
    "Other Income Expense": "Other Income/Expense",
    "Total Other Income Expense Net": "Other Income/Expense",
}

BALANCE_MAP = {
    "Total Assets": "Total Assets",
    "Total Liabilities Net Minority Interest": "Total Liabilities",
    "Stockholders Equity": "Shareholders' Equity",
    "Cash And Cash Equivalents": "Cash & Equivalents",
    "Total Debt": "Total Debt",
    "Current Assets": "Current Assets",
    "Current Liabilities": "Current Liabilities",
    "Accounts Receivable": "Accounts Receivable",
    "Inventory": "Inventory",
    "Goodwill And Other Intangible Assets": "Intangible Assets",
    "Goodwill": "Goodwill",
}

CASHFLOW_MAP = {
    "Operating Cash Flow": "Operating CF",
    "Capital Expenditure": "CapEx",
    "Free Cash Flow": "FCF",
    "Financing Cash Flow": "Financing CF",
}

SCALE_BILLIONS = {
    "Revenue", "COGS", "Gross Profit", "Operating Income", "Net Income",
    "EBITDA", "R&D Expense", "Other Income/Expense",
    "Total Assets", "Total Liabilities", "Shareholders' Equity",
    "Cash & Equivalents", "Total Debt", "Current Assets", "Current Liabilities",
    "Accounts Receivable", "Inventory", "Intangible Assets", "Goodwill",
    "Operating CF", "CapEx", "FCF", "Financing CF",
}

# Display-only metric lists for each statement sub-tab
INCOME_DISPLAY = [
    "Revenue", "Gross Profit", "Operating Income", "Net Income",
    "EBITDA", "R&D Expense", "EPS (Basic)", "EPS (Diluted)",
]
BALANCE_DISPLAY = [
    "Total Assets", "Total Liabilities", "Shareholders' Equity",
    "Cash & Equivalents", "Total Debt", "Current Assets", "Current Liabilities",
    "Accounts Receivable", "Inventory", "Intangible Assets",
]
CASHFLOW_DISPLAY = [
    "Operating CF", "CapEx", "FCF", "Financing CF",
]
DERIVED_DISPLAY = [
    "Gross Margin", "Operating Margin", "Net Margin", "FCF Margin",
    "D/E Ratio", "Current Ratio", "OCF/NI", "Accrual Ratio",
    "DSO", "Inventory Days", "R&D % Revenue",
]


def _quarter_label(dt) -> str:
    q = (dt.month - 1) // 3 + 1
    return f"Q{q} {dt.year}"


def _extract(df_raw, mapping: dict) -> pd.DataFrame:
    if df_raw is None or (hasattr(df_raw, "empty") and df_raw.empty):
        return pd.DataFrame()

    rows = {}
    seen_display: set = set()
    for yf_name, display_name in mapping.items():
        if display_name in seen_display:
            continue
        if yf_name in df_raw.index:
            rows[display_name] = df_raw.loc[yf_name]
            seen_display.add(display_name)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_index(ascending=False)

    for col in df.columns:
        if col in SCALE_BILLIONS:
            df[col] = pd.to_numeric(df[col], errors="coerce") / 1e9

    return df


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.div(b.replace(0, np.nan))


def fetch_fundamentals(symbol: str) -> dict:
    """
    Fetch quarterly financials for `symbol` from yfinance.

    Returns
    -------
    dict with keys:
      income, balance, cashflow  — DataFrames (rows = quarter labels, cols = metrics, values in $B)
      derived                    — DataFrame of quality ratios (same row index)
      quarters                   — all available quarter label strings, most-recent first
      display_quarters           — first 4 quarter labels (for UI tables)
      currency                   — e.g. "USD", "TWD"
      symbol                     — uppercased symbol
      n_quarters                 — number of quarters available
    """
    symbol = symbol.upper()
    ticker = yf.Ticker(symbol)

    income_df  = _extract(getattr(ticker, "quarterly_income_stmt",   None), INCOME_MAP)
    balance_df = _extract(getattr(ticker, "quarterly_balance_sheet", None), BALANCE_MAP)
    cashflow_df = _extract(getattr(ticker, "quarterly_cashflow",     None), CASHFLOW_MAP)

    # ── Reference dates ────────────────────────────────────────────────────
    ref_dates = []
    for df in (income_df, balance_df, cashflow_df):
        if not df.empty:
            ref_dates = sorted(df.index.tolist(), reverse=True)
            break

    if not ref_dates:
        return {
            "income": pd.DataFrame(), "balance": pd.DataFrame(),
            "cashflow": pd.DataFrame(), "derived": pd.DataFrame(),
            "quarters": [], "display_quarters": [],
            "currency": "USD", "symbol": symbol, "n_quarters": 0,
        }

    quarter_labels = [_quarter_label(d) for d in ref_dates]

    def _reindex(df):
        if df.empty:
            return pd.DataFrame(index=quarter_labels)
        df = df.reindex(ref_dates)
        df.index = quarter_labels
        return df

    income_df   = _reindex(income_df)
    balance_df  = _reindex(balance_df)
    cashflow_df = _reindex(cashflow_df)

    # FCF fallback: Operating CF + CapEx (CapEx is stored negative)
    if "FCF" not in cashflow_df.columns or cashflow_df["FCF"].isna().all():
        if "Operating CF" in cashflow_df.columns and "CapEx" in cashflow_df.columns:
            cashflow_df["FCF"] = cashflow_df["Operating CF"] + cashflow_df["CapEx"]

    # ── Derived quality ratios ─────────────────────────────────────────────
    derived = pd.DataFrame(index=quarter_labels)

    def _s(df, col):
        if df.empty or col not in df.columns:
            return pd.Series(np.nan, index=quarter_labels, dtype=float)
        return pd.to_numeric(df[col], errors="coerce")

    rev  = _s(income_df, "Revenue")
    cogs = _s(income_df, "COGS")
    gp   = _s(income_df, "Gross Profit")
    oi   = _s(income_df, "Operating Income")
    ni   = _s(income_df, "Net Income")
    rd   = _s(income_df, "R&D Expense")

    ta  = _s(balance_df, "Total Assets")
    te  = _s(balance_df, "Shareholders' Equity")
    td  = _s(balance_df, "Total Debt")
    ca  = _s(balance_df, "Current Assets")
    cl  = _s(balance_df, "Current Liabilities")
    ar  = _s(balance_df, "Accounts Receivable")
    inv = _s(balance_df, "Inventory")

    ocf = _s(cashflow_df, "Operating CF")
    fcf = _s(cashflow_df, "FCF")

    derived["Gross Margin"]     = _safe_div(gp, rev)
    derived["Operating Margin"] = _safe_div(oi, rev)
    derived["Net Margin"]       = _safe_div(ni, rev)
    derived["FCF Margin"]       = _safe_div(fcf, rev)
    derived["R&D % Revenue"]    = _safe_div(rd, rev)
    derived["D/E Ratio"]        = _safe_div(td, te)
    derived["Current Ratio"]    = _safe_div(ca, cl)
    derived["OCF/NI"]           = _safe_div(ocf, ni)
    derived["Accrual Ratio"]    = _safe_div((ni - ocf), ta)
    derived["DSO"]              = _safe_div(ar, rev / 90)
    derived["Inventory Days"]   = _safe_div(inv, cogs / 90)

    try:
        currency = ticker.info.get("currency", "USD") or "USD"
    except Exception:
        currency = "USD"

    n = len(quarter_labels)
    return {
        "income":           income_df,
        "balance":          balance_df,
        "cashflow":         cashflow_df,
        "derived":          derived,
        "quarters":         quarter_labels,
        "display_quarters": quarter_labels[:4],
        "currency":         currency,
        "symbol":           symbol,
        "n_quarters":       n,
    }
