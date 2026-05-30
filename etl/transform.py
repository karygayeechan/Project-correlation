import pandas as pd


def reshape(df: pd.DataFrame) -> pd.DataFrame:
    tickers = ["AAPL", "GOOGL", "AVGO", "ARM", "TSM"]
    frames = []

    for ticker in tickers:
        cols = [col for col in df.columns if col.endswith(f"_{ticker}")]
        ticker_df = df[["Date"] + cols].copy()
        ticker_df.columns = ["date"] + [col.replace(f"_{ticker}", "").lower().replace(" ", "_") for col in cols]
        ticker_df["symbol"] = ticker
        frames.append(ticker_df)

    result = pd.concat(frames, ignore_index=True)
    return result.dropna(subset=["close"])


if __name__ == "__main__":
    from extract import fetch_raw
    raw = fetch_raw()
    result = reshape(raw)
    print(result.head(10).to_string())
    print(f"\n{len(result)} total rows")
