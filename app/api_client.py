"""
HTTP client that wraps the FastAPI backend.
Exposes the same function signatures as db.py so streamlit_app.py
can swap between them with a single import change.
"""
import os
from datetime import date

import httpx
import pandas as pd

API_URL = os.getenv("API_URL", "http://localhost:8000")
TIMEOUT = 120  # seconds — ETL runs can be slow


def _get(path: str, **params) -> httpx.Response:
    r = httpx.get(f"{API_URL}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _post(path: str, json: dict = None) -> httpx.Response:
    r = httpx.post(f"{API_URL}{path}", json=json or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _delete(path: str) -> httpx.Response:
    r = httpx.delete(f"{API_URL}{path}", timeout=TIMEOUT)
    r.raise_for_status()
    return r


# ─── Read queries (mirror db.py signatures) ───────────────────────────────────

def get_tickers() -> list[str]:
    return _get("/tickers").json()


def get_stock_prices(tickers: list[str], start_date, end_date) -> pd.DataFrame:
    records = _get(
        "/prices",
        tickers=",".join(tickers),
        start_date=str(start_date),
        end_date=str(end_date),
    ).json()
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df


def get_corr_heatmap(tickers: list[str], period: str, end_date) -> pd.DataFrame:
    payload = _get(
        "/correlations/heatmap",
        tickers=",".join(tickers),
        period=period,
        end_date=str(end_date),
    ).json()
    records = payload.get("records", [])
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    matrix = df.pivot(index="symbol_1", columns="symbol_2", values="corr_value")
    matrix.index.name = None
    matrix.columns.name = None
    ordered = [t for t in payload.get("tickers", []) if t in matrix.index]
    if ordered:
        matrix = matrix.loc[ordered, ordered]
    return matrix


def get_rolling_corr(sym1: str, sym2: str, start_date, end_date, window: int = 21) -> pd.Series:
    records = _get(
        "/correlations/rolling",
        sym1=sym1,
        sym2=sym2,
        start_date=str(start_date),
        end_date=str(end_date),
        window=window,
    ).json()
    if not records:
        return pd.Series(dtype=float, name="corr")
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    series = df.set_index("date")["corr"]
    series.name = "corr"
    return series


def get_etl_log(limit: int = 50) -> pd.DataFrame:
    records = _get("/etl/log", limit=limit).json()
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if "run_at" in df.columns:
        df["run_at"] = pd.to_datetime(df["run_at"])
    return df


# ─── Write operations (called from Manage Tickers tab) ────────────────────────

def run_etl(tickers: list[str] = None) -> dict:
    return _post("/etl/run", json={"tickers": tickers}).json()


def add_ticker(symbol: str) -> dict:
    return _post("/tickers", json={"symbol": symbol}).json()


def remove_ticker_from_db(symbol: str) -> dict:
    return _delete(f"/tickers/{symbol}").json()
