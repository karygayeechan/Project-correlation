import os
import sys
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from etl.load import remove_ticker_from_db
from etl.load import run as etl_run
from etl.load import get_connection, get_active_tickers_from_db

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Stock Correlation API",
    description="Backend service for the stock correlation analysis pipeline.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Request / Response models ────────────────────────────────────────────────

class ETLRunRequest(BaseModel):
    tickers: Optional[list[str]] = None

class AddTickerRequest(BaseModel):
    symbol: str

# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    """Check that the API and DB connection are alive."""
    try:
        conn = get_connection()
        conn.close()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unreachable: {e}")

# ─── Tickers ──────────────────────────────────────────────────────────────────

@app.get("/tickers", tags=["Tickers"])
def get_tickers() -> list[str]:
    """Return all ticker symbols currently in the database."""
    return db.get_tickers()


@app.post("/tickers", tags=["Tickers"])
def add_ticker(body: AddTickerRequest):
    """Add a new ticker and run the ETL for all current tickers + the new one."""
    symbol = body.symbol.strip().upper()
    current = db.get_tickers()
    if symbol in current:
        raise HTTPException(status_code=409, detail=f"{symbol} is already in the database.")
    try:
        etl_run(tickers=current + [symbol])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "symbol": symbol, "tickers": db.get_tickers()}


@app.delete("/tickers/{symbol}", tags=["Tickers"])
def delete_ticker(symbol: str):
    """Remove a ticker and all its associated data from the database."""
    symbol = symbol.upper()
    current = db.get_tickers()
    if symbol not in current:
        raise HTTPException(status_code=404, detail=f"{symbol} not found in the database.")
    try:
        remove_ticker_from_db(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "removed": symbol, "tickers": db.get_tickers()}

# ─── Prices ───────────────────────────────────────────────────────────────────

@app.get("/prices", tags=["Prices"])
def get_prices(
    tickers: str = Query(..., description="Comma-separated ticker symbols, e.g. NVDA,GOOGL"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """
    Return daily OHLCV rows for the given tickers and date range.
    Sourced from the `stock_prices` table.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    df = db.get_stock_prices(ticker_list, start_date, end_date)
    if df.empty:
        return []
    df["date"] = df["date"].astype(str)
    return df.to_dict(orient="records")

# ─── Correlations ─────────────────────────────────────────────────────────────

@app.get("/correlations/heatmap", tags=["Correlations"])
def get_corr_heatmap(
    tickers: str = Query(..., description="Comma-separated ticker symbols"),
    period: str = Query("1m", description="'1m' (21 trading days) or '6m' (126 trading days)"),
    end_date: date = Query(default=None),
):
    """
    Compute pairwise Pearson correlations from `stock_prices` up to end_date.
    Returns a flat list of (symbol_1, symbol_2, corr_value) records covering
    all pairs including the diagonal (self-correlation = 1.0).
    """
    if period not in ("1m", "6m"):
        raise HTTPException(status_code=400, detail="period must be '1m' or '6m'")
    if end_date is None:
        end_date = date.today()

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    matrix = db.get_corr_heatmap(ticker_list, period, end_date)

    if matrix.empty:
        return {"tickers": ticker_list, "records": []}

    records = []
    for s1 in matrix.index:
        for s2 in matrix.columns:
            val = matrix.loc[s1, s2]
            if val is not None and str(val) != "nan":
                records.append({"symbol_1": s1, "symbol_2": s2, "corr_value": round(float(val), 4)})

    return {"tickers": list(matrix.columns), "records": records}


@app.get("/correlations/rolling", tags=["Correlations"])
def get_rolling_corr(
    sym1: str = Query(..., description="First ticker symbol"),
    sym2: str = Query(..., description="Second ticker symbol"),
    start_date: date = Query(...),
    end_date: date = Query(...),
    window: int = Query(21, description="Rolling window in trading days"),
):
    """
    Compute rolling Pearson correlation between two tickers over the date range.
    Returns a time series of {date, corr} records.
    """
    series = db.get_rolling_corr(sym1.upper(), sym2.upper(), start_date, end_date, window)
    series = series.dropna()
    if series.empty:
        return []
    return [
        {"date": str(idx.date() if hasattr(idx, "date") else idx), "corr": round(float(val), 4)}
        for idx, val in series.items()
    ]

# ─── ETL ──────────────────────────────────────────────────────────────────────

@app.get("/etl/log", tags=["ETL"])
def get_etl_log(limit: int = Query(50, ge=1, le=500)):
    """Return the most recent ETL run log entries."""
    df = db.get_etl_log(limit)
    if df.empty:
        return []
    df["run_at"] = df["run_at"].astype(str)
    df = df.where(df.notna(), None)
    return df.to_dict(orient="records")


@app.post("/etl/run", tags=["ETL"])
def run_etl(body: ETLRunRequest = ETLRunRequest()):
    """
    Trigger a full ETL run. If `tickers` is omitted, runs for all tickers
    currently in the database. Runs synchronously — expect 15–60s depending
    on yfinance response times.
    """
    tickers = body.tickers
    if tickers is None:
        tickers = db.get_tickers() or None
    try:
        etl_run(tickers=tickers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "tickers": tickers or []}
