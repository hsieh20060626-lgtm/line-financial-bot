"""End-to-end market analysis orchestration for the LINE bot."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from indicators import analyze_technical_indicators
from market_data import fetch_daily_history, fetch_benchmarks
from fundamental import fetch_fundamentals
from chips_analysis import fetch_stock_chips
from news import fetch_symbol_news, fetch_macro_news
from market_score import calculate_market_score
from ai_analysis import explain_analysis

_CACHE = {}
_CACHE_TTL = 300


def run_market_analysis(user_symbol: str) -> dict:
    cache_key = user_symbol.strip().upper()
    cached = _CACHE.get(cache_key)
    if cached and (datetime.now(timezone.utc) - cached['cached_at']).total_seconds() < _CACHE_TTL:
        return cached['payload']
    market_data = fetch_daily_history(user_symbol, period="2y")
    ticker, market = market_data["symbol"], market_data["market"]
    technical = analyze_technical_indicators(market_data["history"])
    fundamental = fetch_fundamentals(ticker, market)
    chips = fetch_stock_chips(user_symbol, os.getenv("FINMIND_TOKEN", "")) if market == "TW" else {
        "available": False, "reason": "美股不適用台股三大法人資料，且本版未接入可驗證的美股資金流來源"}
    if chips.get("available"):
        chips["volume"] = float(market_data["history"]["Volume"].iloc[-1] or 0)
    benchmarks = fetch_benchmarks(market)
    news_items = fetch_symbol_news(user_symbol) + fetch_macro_news()
    score = calculate_market_score(technical, fundamental, chips, benchmarks, news_items)
    payload = {
        "symbol": user_symbol.upper(), "resolved_symbol": ticker, "market": market,
        "as_of": market_data["as_of"], "source": market_data["source"],
        "stale": market_data["stale"], "generated_at": datetime.now(timezone.utc).isoformat(), "technical": technical,
        "fundamental": fundamental, "chips": chips, "benchmarks": benchmarks,
        "news": news_items, "score": score,
    }
    payload["ai"] = explain_analysis(payload)
    score = calculate_market_score(
        technical, fundamental, chips, benchmarks, news_items,
        news_score=payload["ai"].get("news_score")
    )
    payload["score"] = score
    _CACHE[cache_key] = {"cached_at": datetime.now(timezone.utc), "payload": payload}
    return payload


def format_market_analysis(data: dict) -> str:
    score = data["score"]
    lines = [f"📊 川投顧市場訊號分析｜{data['symbol']}",
             f"市場：{'台股' if data['market']=='TW' else '美股'}｜資料日期：{data['as_of']}",
             f"資料來源：{data['source']}",
             f"分析時間：{data.get('generated_at', '未提供')}"]
    if data.get("stale"):
        lines.append("⚠️ 行情資料可能過期，請勿視為即時報價。")
    if score["score"] is None:
        lines.append("總分：資料不足，停止評分")
    else:
        lines.append(f"總分：{score['score']:.1f}/100｜訊號：{score['signal']}")
    lines.append(f"資料完整度：{score['data_completeness']:.0f}%（依有效面向權重計算）")
    lines.append("五大面向（面向｜分數｜分析結果）：")
    labels = {"technical":"技術面 30%", "fundamental":"基本面 20%", "chips":"籌碼面 20%",
              "market":"市場環境 15%", "news":"消息面 15%"}
    for key, label in labels.items():
        value, reasons = score["dimensions"][key]
        score_text = "未評分" if value is None else f"{value:.1f}/100"
        explanation = "；".join(reasons[:2]) if reasons else "目前沒有可用的分析依據"
        lines.append(f"{label}｜{score_text}｜{explanation}")
    daily, weekly = data["technical"]["daily"], data["technical"]["weekly"]
    lines += ["日週線：日 K {0}；週 K {1}（週 K 最新一根可能尚未收完）".format(daily["trend"], weekly["trend"])]
    f = data["fundamental"]
    if f.get("not_applicable"):
        lines.append(f"基本面資料：{f.get('reason', '標的不適用')}")
    else:
        eps_text = f"{f['eps_ttm']:.2f}" if f.get("eps_ttm") is not None else "缺失"
        pe_text = f"{f['trailing_pe']:.2f}" if f.get("trailing_pe") is not None else "缺失"
        revenue_text = f"{f['revenue_growth']*100:.1f}%" if f.get("revenue_growth") is not None else "缺失"
        earnings_text = f"{f['earnings_growth']*100:.1f}%" if f.get("earnings_growth") is not None else "缺失"
        lines.append(f"基本面明細：EPS {eps_text}；本益比 {pe_text}；營收年增 {revenue_text}；獲利年增 {earnings_text}")
        lines.append(f"基本面來源：{f.get('source', '未提供')}｜資料日期：{f.get('as_of') or '未提供'}")
        if f.get("missing_fields"):
            lines.append("基本面缺失欄位：" + "、".join(f["missing_fields"]))
        for warning in f.get("fetch_warnings", [])[:2]:
            lines.append("基本面取得警告：" + warning)
    if data["chips"].get("available"):
        lines.append(f"籌碼資料日期：{data['chips'].get('date')}（單位：股）")
    else:
        lines.append(f"籌碼面缺失：{data['chips'].get('reason','無可用資料')}")
    lines.append("近期新聞：")
    for item in data["news"][:4]:
        lines.append(f"・{item['title'][:85]}（{item.get('published', '時間未提供')}｜{item.get('source', '來源未提供')}）")
    if not data["news"]:
        lines.append("・目前無可驗證新聞")
    lines.append("AI 解讀：" + data["ai"]["text"])
    lines.append("※ 本分析為規則化研究輔助，不構成投資建議；評分未經歷史回測，不代表未來報酬。")
    return "\n".join(lines)[:4800]
