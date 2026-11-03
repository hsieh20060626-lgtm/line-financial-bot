"""Fundamental data adapter with Yahoo profile + quarterly-statement fallbacks.
Missing values remain None; partial data is reported explicitly.
"""
from __future__ import annotations
from typing import Any
import math
import yfinance as yf


def _number(value):
    try:
        if value is None:
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _statement_value(statement, names):
    if statement is None or getattr(statement, "empty", True):
        return []
    wanted = {name.casefold() for name in names}
    for row_name in statement.index:
        if str(row_name).casefold() in wanted:
            row = statement.loc[row_name]
            return [(period, number) for period, value in row.items()
                    if (number := _number(value)) is not None]
    return []


def _date_text(value):
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value)[:10]
    except Exception:
        return str(value)[:10]


def fetch_fundamentals(symbol: str, market: str) -> dict[str, Any]:
    errors = []
    try:
        ticker = yf.Ticker(symbol)
    except Exception as exc:
        return {"available": False, "reason": f"無法建立 Yahoo Finance 查詢：{type(exc).__name__}",
                "missing_fields": ["EPS（近四季）", "本益比", "營收年增率", "獲利年增率", "獲利率"],
                "source": "Yahoo Finance"}

    info = {}
    try:
        info = ticker.get_info() or {}
    except Exception as exc:
        errors.append(f"公司資訊 API 失敗（{type(exc).__name__}）")

    if str(info.get("quoteType", "")).upper() == "ETF":
        return {"available": False, "not_applicable": True,
                "reason": "ETF 不適用一般公司 EPS／本益比評分",
                "missing_fields": [], "source": "Yahoo Finance"}

    def info_number(*keys):
        for key in keys:
            value = _number(info.get(key))
            if value is not None:
                return value
        return None

    eps = info_number("trailingEps")
    pe = info_number("trailingPE")
    forward_pe = info_number("forwardPE")
    revenue_growth = info_number("revenueGrowth")
    earnings_growth = info_number("earningsGrowth")
    profit_margin = info_number("profitMargins")
    market_price = info_number("currentPrice", "regularMarketPrice")

    quarterly = None
    try:
        quarterly = ticker.quarterly_income_stmt
    except Exception as exc:
        errors.append(f"季度財報 API 失敗（{type(exc).__name__}）")

    eps_rows = _statement_value(quarterly, (
        "Diluted EPS", "Basic EPS", "Diluted Earnings Per Share", "Basic Earnings Per Share"))
    revenue_rows = _statement_value(quarterly, ("Total Revenue", "Operating Revenue", "Revenue"))
    net_income_rows = _statement_value(quarterly, ("Net Income", "Net Income Common Stockholders"))
    for rows in (eps_rows, revenue_rows, net_income_rows):
        rows.sort(key=lambda item: item[0], reverse=True)

    if eps is None and len(eps_rows) >= 4:
        eps = sum(value for _, value in eps_rows[:4])
    if revenue_growth is None and len(revenue_rows) >= 5 and revenue_rows[4][1]:
        revenue_growth = revenue_rows[0][1] / revenue_rows[4][1] - 1
    if earnings_growth is None and len(net_income_rows) >= 5 and net_income_rows[4][1]:
        earnings_growth = net_income_rows[0][1] / net_income_rows[4][1] - 1
    if profit_margin is None and revenue_rows and net_income_rows:
        revenue_by_period = dict(revenue_rows)
        for period, net_income in net_income_rows:
            revenue = revenue_by_period.get(period)
            if revenue:
                profit_margin = net_income / revenue
                break
    if pe is None and market_price is not None and eps is not None and eps > 0:
        pe = market_price / eps

    values = {"eps_ttm": eps, "trailing_pe": pe, "forward_pe": forward_pe,
              "revenue_growth": revenue_growth, "earnings_growth": earnings_growth,
              "profit_margin": profit_margin}
    missing = [label for key, label in (
        ("eps_ttm", "EPS（近四季）"), ("trailing_pe", "本益比"),
        ("revenue_growth", "營收年增率"), ("earnings_growth", "獲利年增率"),
        ("profit_margin", "獲利率")) if values[key] is None]
    available = any(value is not None for value in
                    (eps, pe, revenue_growth, earnings_growth, profit_margin))
    sources = ["Yahoo Finance 公司資訊"] if info else []
    if eps_rows or revenue_rows or net_income_rows:
        sources.append("Yahoo Finance 季度財報")
    as_of = info.get("mostRecentQuarter") or (revenue_rows[0][0] if revenue_rows else None)
    return {
        "available": available, **values, "currency": info.get("currency"),
        "sector": info.get("sector"), "market_cap": info_number("marketCap"),
        "as_of": _date_text(as_of),
        "source": " + ".join(sources) if sources else "Yahoo Finance（未取得有效欄位）",
        "missing_fields": missing,
        "reason": ("部分欄位取得成功" if available and missing else
                   "基本面欄位完整" if available else
                   "公司資訊與季度財報均未提供可評分欄位"),
        "fetch_warnings": errors,
        "note": "缺少欄位不以 0 代替；僅以成功取得的欄位評分。",
    }
