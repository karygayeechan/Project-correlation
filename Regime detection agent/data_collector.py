"""
Fetches macro regime-detection indicators:
  1. 10Y Treasury yield   — ^TNX via yfinance
  2. 10Y TIPS real yield  — DFII10 via FRED
  3. Nasdaq-100 breadth   — % of NDX-100 components above 200DMA (yfinance)
  4. VIX                  — ^VIX via yfinance
  5. SMH/QQQ relative strength — price ratio + rolling 252-day z-score (yfinance)

Entry point: fetch_indicators(lookback_days=365) -> pd.DataFrame
"""
import os
import datetime
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# NDX-100 components (as of Q2 2026; update when index rebalances quarterly)
NDX_100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "COST",
    "NFLX", "AMD",  "ADBE", "QCOM", "INTU", "TXN",  "CSCO", "ORCL", "PYPL", "EBAY",
    "AMAT", "MU",   "LRCX", "KLAC", "MRVL", "ADI",  "ON",   "MCHP", "NXPI", "ASML",
    "PANW", "SNPS", "CDNS", "FTNT", "CRWD", "ZS",   "DDOG", "WDAY", "TEAM", "MSTR",
    "AMGN", "GILD", "REGN", "VRTX", "IDXX", "ISRG", "MRNA", "DXCM", "BIIB", "ILMN",
    "MNST", "SBUX", "MDLZ", "DLTR", "CPRT", "ROST", "KDP",  "ALGN", "LULU", "INTC",
    "PCAR", "FAST", "ODFL", "CTAS", "VRSK", "CSGP", "BKR",  "CEG",  "AEP",  "XEL",
    "CHTR", "CMCSA","TMUS", "SIRI", "EA",   "HON",  "ROP",  "GEHC", "FANG", "ENPH",
    "MELI", "PDD",  "BIDU", "ABNB", "PLTR", "TTD",  "ZM",   "APP",  "DASH", "SMCI",
    "ARM",  "MDB",  "SNOW", "UBER", "COIN", "HOOD", "NTES", "JD",
]

_DMA_PERIOD = 200
# Extra calendar days to fetch for rolling warmup.
# 252 trading days (z-score window) ≈ 365 cal days; 400 gives comfortable headroom.
_DMA_BUFFER = 400


def fetch_indicators(lookback_days: int = 365) -> pd.DataFrame:
    """
    Returns a tidy DataFrame indexed by date with columns:
        treasury_10y    — 10Y nominal yield (%)
        tips_10y        — 10Y TIPS real yield (%)
        nasdaq_breadth  — % of NDX-100 stocks above their 200DMA
        vix             — CBOE VIX spot level
        smh_qqq_ratio   — SMH/QQQ price ratio
        smh_qqq_zscore  — ratio z-score vs rolling 252-day window

    Rows cover approximately the past `lookback_days` calendar days.
    """
    end = datetime.date.today()
    fetch_start = end - datetime.timedelta(days=lookback_days + _DMA_BUFFER)
    display_cutoff = pd.Timestamp(end - datetime.timedelta(days=lookback_days))

    # ── yfinance batch download ─────────────────────────────────────────────
    yf_tickers = ["^TNX", "^VIX", "SMH", "QQQ"] + NDX_100
    raw = yf.download(
        yf_tickers,
        start=str(fetch_start),
        end=str(end),
        auto_adjust=True,
        progress=False,
    )
    # raw["Close"] is a DataFrame with one column per ticker when multiple tickers are passed
    close = raw["Close"].copy()
    close.index = pd.to_datetime(close.index)
    # ^TNX / ^VIX trade on some CBOE holidays when stocks are closed, adding NaN rows for
    # equities. Filter to stock market trading days using QQQ as the calendar reference.
    close = close[close["QQQ"].notna()].copy()

    # ── 1. 10Y Treasury yield ───────────────────────────────────────────────
    treasury_10y = close["^TNX"].rename("treasury_10y")

    # ── 4. VIX ─────────────────────────────────────────────────────────────
    vix = close["^VIX"].rename("vix")

    # ── 5. SMH / QQQ relative strength ─────────────────────────────────────
    ratio = (close["SMH"] / close["QQQ"]).rename("smh_qqq_ratio")
    rolling_mean = ratio.rolling(252).mean()
    rolling_std  = ratio.rolling(252).std()
    zscore = ((ratio - rolling_mean) / rolling_std).rename("smh_qqq_zscore")

    # ── 3. Nasdaq-100 breadth ───────────────────────────────────────────────
    ndx_cols = [t for t in NDX_100 if t in close.columns]
    ndx_close = close[ndx_cols]
    above_dma = (ndx_close > ndx_close.rolling(_DMA_PERIOD).mean()).sum(axis=1)
    valid_count = ndx_close.notna().sum(axis=1)
    nasdaq_breadth = (above_dma / valid_count * 100).rename("nasdaq_breadth")

    # ── 2. 10Y TIPS real yield (FRED) ───────────────────────────────────────
    from fredapi import Fred
    fred = Fred(api_key=os.environ["FRED_API_KEY"])
    tips_raw = fred.get_series(
        "DFII10",
        observation_start=str(fetch_start),
        observation_end=str(end),
    )
    tips_10y = tips_raw.rename("tips_10y")
    tips_10y.index = pd.to_datetime(tips_10y.index)

    # ── Assemble & trim to display window ───────────────────────────────────
    df = pd.concat([treasury_10y, tips_10y, nasdaq_breadth, vix, ratio, zscore], axis=1, sort=True)
    df = df.sort_index()
    df = df[df.index >= display_cutoff]
    df.index.name = "date"
    return df.dropna(how="all")


if __name__ == "__main__":
    df = fetch_indicators(lookback_days=365)
    print(df.tail(10).to_string())
    print(f"\nShape:      {df.shape}")
    print(f"Columns:    {list(df.columns)}")
    print(f"Date range: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"\nLatest values:\n{df.iloc[-1].to_string()}")
