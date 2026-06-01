import yfinance as yf
import pandas as pd

TICKERS = ["NVDA", "GOOGL", "AVGO", "ARM", "TSM"]
PERIOD = "1y"
INTERVAL = "1d"


def fetch_raw(tickers: list[str] = TICKERS) -> pd.DataFrame:
    df = yf.download(
        tickers,
        period=PERIOD,
        interval=INTERVAL,
        auto_adjust=False,
        progress=False,
    )
    # flatten multi-level columns -> (price_type, ticker) tuples
    df.columns = ["_".join(col).strip() for col in df.columns]
    df = df.reset_index()
    return df


def fetch_ticker_info(tickers: list[str] = TICKERS) -> list[dict]:
    records = []
    for symbol in tickers:
        info = yf.Ticker(symbol).info
        records.append({
            "symbol": symbol,
            "company_name": info.get("longName", symbol),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
        })
    return records


if __name__ == "__main__":
    df = fetch_raw()
    print(f"Fetched {len(df)} rows, {len(df.columns)} columns")
    print(df.head(3).to_string())
