"""Technical indicators for swing-trading analysis.

Input: a pandas DataFrame containing OHLCV columns (case-insensitive).
The module computes daily and resampled weekly indicators. It does not make
investment recommendations or fetch market data.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


PERIODS = (5, 10, 20, 60)


def _normalise_ohlcv(data: pd.DataFrame) -> pd.DataFrame:
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("行情資料為空，無法計算技術指標。")

    frame = data.copy()
    # yfinance may return MultiIndex columns, e.g. ('Close', '2330.TW').
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [
            next((str(part) for part in col if str(part).lower() in
                  {"open", "high", "low", "close", "adj close", "volume"}),
                 str(col[0]))
            for col in frame.columns
        ]

    aliases = {str(col).strip().lower(): col for col in frame.columns}
    rename = {}
    for wanted in ("Open", "High", "Low", "Close", "Volume"):
        source = aliases.get(wanted.lower())
        if source is None:
            raise ValueError(f"行情資料缺少必要欄位：{wanted}")
        rename[source] = wanted
    frame = frame.rename(columns=rename)
    frame = frame[["Open", "High", "Low", "Close", "Volume"]]
    for col in frame.columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["Close"]).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if frame.empty:
        raise ValueError("沒有可用的收盤價資料。")
    return frame


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0)
    result = result.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return result


def calculate_indicators(data: pd.DataFrame, timeframe: str = "daily") -> Dict[str, Any]:
    """Calculate MA, RSI, MACD and volume/trend context for an OHLCV frame."""
    frame = _normalise_ohlcv(data)
    close = frame["Close"]
    volume = frame["Volume"]

    for period in PERIODS:
        frame[f"MA{period}"] = close.rolling(period, min_periods=period).mean()

    frame["RSI14"] = _rsi(close, 14)
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    frame["MACD"] = ema12 - ema26
    frame["MACD_signal"] = frame["MACD"].ewm(
        span=9, adjust=False, min_periods=9
    ).mean()
    frame["MACD_hist"] = frame["MACD"] - frame["MACD_signal"]
    frame["Volume_MA20"] = volume.rolling(20, min_periods=20).mean()
    frame["Volume_ratio"] = frame["Volume"] / frame["Volume_MA20"].replace(0, np.nan)

    last = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) > 1 else None
    ma20 = last.get("MA20", np.nan)
    ma60 = last.get("MA60", np.nan)
    close_value = last["Close"]

    ma_values = [last.get(f"MA{p}", np.nan) for p in PERIODS]
    valid_ma = all(pd.notna(value) for value in ma_values)
    bullish_stack = valid_ma and ma_values[0] > ma_values[1] > ma_values[2] > ma_values[3]
    bearish_stack = valid_ma and ma_values[0] < ma_values[1] < ma_values[2] < ma_values[3]

    if pd.notna(ma20) and pd.notna(ma60):
        if close_value > ma20 > ma60:
            trend = "bullish"
        elif close_value < ma20 < ma60:
            trend = "bearish"
        else:
            trend = "mixed"
    elif pd.notna(ma20):
        trend = "above_ma20" if close_value > ma20 else "below_ma20"
    else:
        trend = "insufficient_data"

    def number(value: Any) -> Optional[float]:
        return None if pd.isna(value) else round(float(value), 4)

    def direction(current: Any, prior: Any) -> Optional[str]:
        if pd.isna(current) or pd.isna(prior):
            return None
        if current > prior:
            return "rising"
        if current < prior:
            return "falling"
        return "flat"

    return {
        "timeframe": timeframe,
        "as_of": str(frame.index[-1].date()) if hasattr(frame.index[-1], "date") else str(frame.index[-1]),
        "bars": int(len(frame)),
        "data_sufficient": bool(len(frame) >= 60),
        "close": number(close_value),
        "moving_averages": {f"MA{p}": number(last.get(f"MA{p}")) for p in PERIODS},
        "price_vs_ma20": (
            "above" if pd.notna(ma20) and close_value > ma20 else
            "below" if pd.notna(ma20) else None
        ),
        "price_vs_ma60": (
            "above" if pd.notna(ma60) and close_value > ma60 else
            "below" if pd.notna(ma60) else None
        ),
        "ma_alignment": (
            "bullish" if bullish_stack else "bearish" if bearish_stack else "mixed_or_insufficient"
        ),
        "rsi14": number(last.get("RSI14")),
        "macd": number(last.get("MACD")),
        "macd_signal": number(last.get("MACD_signal")),
        "macd_histogram": number(last.get("MACD_hist")),
        "macd_histogram_direction": direction(
            last.get("MACD_hist"), previous.get("MACD_hist") if previous is not None else np.nan
        ),
        "volume": number(last.get("Volume")),
        "volume_ma20": number(last.get("Volume_MA20")),
        "volume_ratio": number(last.get("Volume_ratio")),
        "trend": trend,
    }


def calculate_daily_indicators(data: pd.DataFrame) -> Dict[str, Any]:
    return calculate_indicators(data, timeframe="daily")


def resample_weekly(data: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily bars into Friday-ending weekly OHLCV bars."""
    frame = _normalise_ohlcv(data)
    weekly = frame.resample("W-FRI", label="right", closed="right").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    })
    return weekly.dropna(subset=["Close"])


def calculate_weekly_indicators(data: pd.DataFrame) -> Dict[str, Any]:
    weekly = resample_weekly(data)
    return calculate_indicators(weekly, timeframe="weekly")


def compare_timeframes(daily: Dict[str, Any], weekly: Dict[str, Any]) -> Dict[str, Any]:
    daily_trend = daily.get("trend")
    weekly_trend = weekly.get("trend")
    aligned = daily_trend in {"bullish", "bearish"} and daily_trend == weekly_trend
    conflicting = (
        daily_trend in {"bullish", "bearish"}
        and weekly_trend in {"bullish", "bearish"}
        and daily_trend != weekly_trend
    )
    if aligned:
        interpretation = "daily_weekly_aligned"
    elif conflicting:
        interpretation = "daily_weekly_conflict"
    else:
        interpretation = "insufficient_or_mixed"
    return {
        "daily_trend": daily_trend,
        "weekly_trend": weekly_trend,
        "aligned": bool(aligned),
        "conflicting": bool(conflicting),
        "interpretation": interpretation,
    }


def analyze_technical_indicators(data: pd.DataFrame) -> Dict[str, Any]:
    """Return daily + weekly indicators and their trend agreement."""
    daily = calculate_daily_indicators(data)
    weekly = calculate_weekly_indicators(data)
    return {
        "daily": daily,
        "weekly": weekly,
        "timeframe_comparison": compare_timeframes(daily, weekly),
        "source_note": "Indicators are calculated from the supplied OHLCV data; verify its source and update time.",
        "caveat": "The latest weekly candle may be incomplete until the trading week closes.",
    }
