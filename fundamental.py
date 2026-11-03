"""Fundamental data adapter. Missing values remain None; never imputed as zero."""
from __future__ import annotations

from typing import Any
import yfinance as yf


def fetch_fundamentals(symbol: str, market: str) -> dict[str, Any]:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.get_info() or {}
    except Exception as exc:
        return {"available": False, "reason": f"Yahoo Finance fundamentals unavailable: {type(exc).__name__}",
                "source": "Yahoo Finance"}
    if info.get("quoteType") == "ETF":
        return {"available": False, "not_applicable": True, "reason": "ETF 不適用一般公司 EPS／本益比評分", "source": "Yahoo Finance"}

    def number(*keys):
        for key in keys:
            value = info.get(key)
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                pass
        return None
    eps = number("trailingEps")
    pe = number("trailingPE")
    revenue_growth = number("revenueGrowth")
    earnings_growth = number("earningsGrowth")
    profit_margin = number("profitMargins")
    return {
        "available": any(v is not None for v in (eps, pe, revenue_growth, earnings_growth, profit_margin)),
        "eps_ttm": eps, "trailing_pe": pe, "forward_pe": number("forwardPE"),
        "revenue_growth": revenue_growth, "earnings_growth": earnings_growth,
        "profit_margin": profit_margin,
        "currency": info.get("currency"), "sector": info.get("sector"),
        "market_cap": number("marketCap"),
        "as_of": info.get("mostRecentQuarter") or info.get("lastFiscalYearEnd"),
        "source": "Yahoo Finance company profile / financial statements",
        "note": "ETF、指數或缺少公司財報的標的可能不適用 EPS/P/E；不以零分代替缺失資料。",
    }
