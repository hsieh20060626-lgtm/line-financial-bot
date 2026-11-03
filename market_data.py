"""Market data adapters for Taiwan and US swing analysis.

Yahoo Finance is used for daily OHLCV and benchmark data. Returned metadata
always identifies the provider and the last available trading date.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

TW_TZ = timezone(timedelta(hours=8))
TW_CODE_RE = __import__("re").compile(r"^\d{4,6}[A-Z]?$")


def normalize_symbol(symbol: str) -> tuple[str, str]:
    raw = symbol.strip().upper()
    if TW_CODE_RE.fullmatch(raw):
        for suffix in (".TW", ".TWO"):
            try:
                frame = yf.Ticker(raw + suffix).history(period="5d", auto_adjust=False, timeout=12)
                if not frame.empty:
                    return raw + suffix, "TW"
            except Exception:
                continue
        return raw + ".TW", "TW"
    return raw, "US"


def _clean_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [str(c[0]) for c in result.columns]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in result.columns for c in required):
        return pd.DataFrame()
    result = result[required].apply(pd.to_numeric, errors="coerce")
    result = result.dropna(subset=["Close"]).sort_index()
    return result[~result.index.duplicated(keep="last")]


def fetch_daily_history(symbol: str, period: str = "2y") -> dict[str, Any]:
    ticker, market = normalize_symbol(symbol)
    raw = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False, timeout=15)
    frame = _clean_history(raw)
    if frame.empty:
        raise ValueError(f"無法取得 {symbol} 的日 K 資料（Yahoo Finance: {ticker}）。")
    last_index = frame.index[-1]
    last_date = last_index.date().isoformat() if hasattr(last_index, "date") else str(last_index)
    now = datetime.now(TW_TZ if market == "TW" else timezone.utc)
    try:
        last_dt = pd.Timestamp(last_index)
        if last_dt.tzinfo is None:
            last_dt = last_dt.tz_localize("UTC")
        age_days = max(0, (now.date() - last_dt.date()).days)
    except Exception:
        age_days = None
    return {
        "symbol": ticker, "market": market, "history": frame,
        "as_of": last_date, "age_days": age_days,
        "stale": bool(age_days is not None and age_days > (4 if market == "TW" else 5)),
        "source": "Yahoo Finance / yfinance",
    }


def fetch_benchmarks(market: str) -> dict[str, Any]:
    symbols = ({"TAIEX": "^TWII", "TAIFEX": "TX=F", "SP500": "^GSPC", "NASDAQ": "^IXIC", "SOX": "^SOX", "VIX": "^VIX"}
               if market == "TW" else
               {"SP500": "^GSPC", "NASDAQ": "^IXIC", "SOX": "^SOX", "VIX": "^VIX"})
    result = {}
    for name, ticker in symbols.items():
        try:
            hist = _clean_history(yf.Ticker(ticker).history(period="10d", auto_adjust=False, timeout=10))
            if len(hist) >= 2:
                prev, last = float(hist.Close.iloc[-2]), float(hist.Close.iloc[-1])
                result[name] = {"symbol": ticker, "close": last, "change_pct": (last / prev - 1) * 100,
                                "as_of": str(hist.index[-1].date())}
        except Exception:
            continue
    return result
