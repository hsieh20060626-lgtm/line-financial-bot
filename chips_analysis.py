"""Institutional flow adapter for Taiwan listed equities via FinMind."""
from __future__ import annotations
from datetime import date, timedelta
import requests


def fetch_stock_chips(stock_id: str, token: str = "") -> dict:
    if not (stock_id.isdigit() and 4 <= len(stock_id) <= 6):
        return {"available": False, "reason": "目前只有台股法人資料來源"}
    params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell",
              "data_id": stock_id, "start_date": (date.today()-timedelta(days=45)).isoformat()}
    if token:
        params["token"] = token
    try:
        response = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=12)
        response.raise_for_status()
        rows = response.json().get("data", [])
    except Exception as exc:
        return {"available": False, "reason": f"FinMind 查詢失敗：{type(exc).__name__}"}
    if not rows:
        return {"available": False, "reason": "FinMind 沒有回傳法人資料"}
    latest_date = max(str(row.get("date", "")) for row in rows)
    day_rows = [r for r in rows if str(r.get("date", "")) == latest_date]
    totals = {"foreign_net": None, "trust_net": None, "dealer_net": None}
    buckets = {"foreign_net": [0, 0], "trust_net": [0, 0], "dealer_net": [0, 0]}
    seen = set()
    for row in day_rows:
        name = str(row.get("name", ""))
        key = None
        if "Foreign" in name and "Self" not in name: key = "foreign_net"
        elif "Investment_Trust" in name: key = "trust_net"
        elif "Dealer" in name: key = "dealer_net"
        if key:
            try:
                buckets[key][0] += float(row.get("buy", 0) or 0)
                buckets[key][1] += float(row.get("sell", 0) or 0)
                seen.add(key)
            except (TypeError, ValueError):
                continue
    for key in seen:
        totals[key] = buckets[key][0] - buckets[key][1]
    return {"available": bool(seen), **totals, "date": latest_date,
            "source": "FinMind TaiwanStockInstitutionalInvestorsBuySell",
            "note": "單位為股；只計算最近一個有資料的交易日"}
