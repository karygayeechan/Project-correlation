import pandas as pd


def reshape(df: pd.DataFrame, tickers=None) -> pd.DataFrame:
    if tickers is None:
        tickers = ["NVDA", "GOOGL", "AVGO", "ARM", "TSM", "JPM", "BAC"]
    frames = []

    for ticker in tickers:
        cols = [col for col in df.columns if col.endswith(f"_{ticker}")]
        ticker_df = df[["Date"] + cols].copy()
        ticker_df.columns = ["date"] + [col.replace(f"_{ticker}", "").lower().replace(" ", "_") for col in cols]
        ticker_df["symbol"] = ticker
        frames.append(ticker_df)

    result = pd.concat(frames, ignore_index=True)
    return result.dropna(subset=["close"])


def compute_correlations(long_df: pd.DataFrame) -> pd.DataFrame:
    df = long_df.sort_values("date").copy()
    df["daily_return"] = df.groupby("symbol")["adj_close"].pct_change()

    pivot = df.pivot(index="date", columns="symbol", values="daily_return")
    pivot.columns.name = None

    periods = {"6m": 126, "12m": 252, "24m": 504}
    frames = []

    for period_name, n_days in periods.items():
        corr_matrix = pivot.tail(n_days).corr()
        stacked = corr_matrix.stack().reset_index()
        stacked.columns = ["symbol_1", "symbol_2", "corr_value"]
        stacked = stacked[stacked["symbol_1"] < stacked["symbol_2"]].copy()
        stacked["period"] = period_name
        frames.append(stacked)

    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    from extract import fetch_raw
    raw = fetch_raw()
    result = reshape(raw)
    print(result.head(10).to_string())
    print(f"\n{len(result)} total rows")
    corr = compute_correlations(result)
    print(f"\nCorrelations ({len(corr)} rows):")
    print(corr.to_string())
