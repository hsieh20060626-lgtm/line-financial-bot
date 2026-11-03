"""End-to-end market analysis orchestration and LINE Flex report formatting."""
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


def _text_component(text: str, size: str = "sm", color: str = "#333333",
                    weight: str = "regular", flex: int | None = None,
                    align: str = "start") -> dict:
    component = {
        "type": "text", "text": str(text), "size": size, "color": color,
        "weight": weight, "wrap": True, "align": align,
    }
    if flex is not None:
        component["flex"] = flex
    return component


def _table_row(item: str, score: str, explanation: str, header: bool = False) -> dict:
    """每個分析面向一列，固定由左至右：項目、分數、分析結果。"""
    if header:
        color, size, weight = "#EAF0F6", "xs", "bold"
        item, score, explanation = "分析項目", "分數", "分析結果"
    else:
        color, size, weight = "#FFFFFF", "sm", "regular"
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "paddingAll": "sm",
        "backgroundColor": color,
        "contents": [
            _text_component(item, size, "#34495E" if header else "#333333",
                            "bold" if header else weight, 3),
            _text_component(score, size, "#34495E" if header else "#1F618D",
                            "bold", 2, "center"),
            _text_component(explanation, size, "#34495E" if header else "#333333",
                            weight, 7),
        ],
    }


def _section_title(text: str) -> dict:
    return {
        "type": "text", "text": text, "size": "md", "weight": "bold",
        "color": "#2C3E50", "margin": "lg", "wrap": True,
    }


def format_market_analysis(data: dict) -> dict:
    """回傳 LINE Flex Message 卡片；五大面向固定使用同一張三欄橫向表格。"""
    score = data["score"]
    symbol = data["symbol"]
    market_name = "台股" if data["market"] == "TW" else "美股"
    signal = score.get("signal") or "資料不足"
    total = "資料不足" if score.get("score") is None else f'{score["score"]:.1f}/100'
    completeness = f'{score["data_completeness"]:.0f}%'
    stale = bool(data.get("stale"))

    labels = {
        "technical": "技術面",
        "fundamental": "基本面",
        "chips": "籌碼面",
        "market": "市場環境",
        "news": "消息面",
    }
    weights = {"technical": "30%", "fundamental": "20%", "chips": "20%",
               "market": "15%", "news": "15%"}

    rows = []
    for key, label in labels.items():
        value, reasons = score["dimensions"][key]
        score_text = "未評分" if value is None else f"{value:.1f}"
        explanation = "；".join(reasons[:2]) if reasons else "目前沒有可用的分析依據"
        rows.append(_table_row(f"{label} {weights[key]}", score_text, explanation))

    daily = data["technical"]["daily"]
    weekly = data["technical"]["weekly"]
    fundamental = data["fundamental"]
    chips = data["chips"]
    news = data.get("news", [])[:4]
    ai_text = (data.get("ai") or {}).get("text") or "AI 解讀暫時不可用，請以原始資料為準。"

    body = [
        _text_component(f"總分：{total}　訊號：{signal}", "lg", "#C0392B" if signal in ("偏空", "強勢偏空") else "#2C3E50", "bold"),
        _text_component(f"資料完整度：{completeness}（依有效面向權重計算）", "xs", "#777777"),
        {"type": "separator", "margin": "md"},
        _table_row("", "", "", header=True),
        *rows,
        {"type": "separator", "margin": "md"},
        _section_title("日 K／週 K"),
        _text_component(f'日 K：{daily.get("trend", "未提供")}　｜　週 K：{weekly.get("trend", "未提供")}（週 K 最新一根可能尚未收完）', "sm"),
    ]

    if fundamental.get("not_applicable"):
        body += [
            _section_title("基本面"),
            _text_component(fundamental.get("reason", "標的不適用一般公司基本面指標。"), "sm"),
        ]
    else:
        eps = f'{fundamental["eps_ttm"]:.2f}' if fundamental.get("eps_ttm") is not None else "缺失"
        pe = f'{fundamental["trailing_pe"]:.2f}' if fundamental.get("trailing_pe") is not None else "缺失"
        revenue = f'{fundamental["revenue_growth"] * 100:.1f}%' if fundamental.get("revenue_growth") is not None else "缺失"
        earnings = f'{fundamental["earnings_growth"] * 100:.1f}%' if fundamental.get("earnings_growth") is not None else "缺失"
        body += [
            _section_title("基本面明細"),
            _text_component(f"EPS：{eps}　｜　本益比：{pe}", "sm"),
            _text_component(f"營收年增：{revenue}　｜　獲利年增：{earnings}", "sm"),
            _text_component(f'來源：{fundamental.get("source", "未提供")}　｜　資料日期：{fundamental.get("as_of") or "未提供"}', "xs", "#777777"),
        ]
        if fundamental.get("missing_fields"):
            body.append(_text_component("缺失欄位：" + "、".join(fundamental["missing_fields"]), "xs", "#B9770E"))
        for warning in fundamental.get("fetch_warnings", [])[:2]:
            body.append(_text_component("取得警告：" + warning, "xs", "#B9770E"))

    if chips.get("available"):
        body += [
            _section_title("籌碼資料"),
            _text_component(f'資料日期：{chips.get("date")}（單位：股）', "sm"),
        ]
    else:
        body += [
            _section_title("籌碼資料"),
            _text_component(chips.get("reason", "目前無可用資料"), "sm"),
        ]

    body.append(_section_title("近期新聞"))
    if news:
        for item in news:
            body.append(_text_component(
                f'・{item.get("title", "未提供標題")[:100]}（{item.get("published", "時間未提供")}｜{item.get("source", "來源未提供")}）',
                "xs", "#444444"
            ))
    else:
        body.append(_text_component("目前無可驗證新聞", "xs", "#777777"))

    body += [
        _section_title("AI 綜合解讀"),
        _text_component(ai_text[:1800], "sm"),
        {"type": "separator", "margin": "lg"},
        _text_component("資料來源：" + str(data.get("source", "未提供")), "xs", "#777777"),
        _text_component("分析時間：" + str(data.get("generated_at", "未提供")), "xs", "#777777"),
    ]
    if stale:
        body.append(_text_component("⚠️ 行情資料可能過期，請勿視為即時報價。", "xs", "#B9770E"))
    body.append(_text_component(
        "※ 本分析為規則化研究輔助，不構成投資建議；評分未經歷史回測，不代表未來報酬。",
        "xs", "#888888"
    ))

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#263B53",
            "paddingAll": "lg",
            "contents": [
                _text_component(f"📊 川投顧市場訊號分析｜{symbol}", "lg", "#FFFFFF", "bold"),
                _text_component(f"{market_name}｜資料日期：{data.get('as_of', '未提供')}", "xs", "#FFFFFF"),
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "md",
            "spacing": "xs",
            "contents": body,
        },
    }

    # Flex 無法建立時由 app.py 自動退回純文字；備用文字也維持同一列三欄的閱讀順序。
    plain_lines = [
        f"📊 川投顧市場訊號分析｜{symbol}",
        f"市場：{market_name}｜資料日期：{data.get('as_of', '未提供')}",
        f"總分：{total}｜訊號：{signal}",
        f"資料完整度：{completeness}",
        "分析項目｜分數｜分析結果",
    ]
    for key, label in labels.items():
        value, reasons = score["dimensions"][key]
        score_text = "未評分" if value is None else f"{value:.1f}"
        plain_lines.append(f"{label} {weights[key]}｜{score_text}｜{'；'.join(reasons[:2]) if reasons else '目前沒有可用的分析依據'}")
    plain_lines += [
        f'日週線：日 K {daily.get("trend", "未提供")}；週 K {weekly.get("trend", "未提供")}',
        "AI 解讀：" + ai_text,
        "※ 本分析為規則化研究輔助，不構成投資建議；評分未經歷史回測，不代表未來報酬。",
    ]
    return {
        "bubble": bubble,
        "alt": f"川投顧市場訊號分析 {symbol}，總分 {total}，訊號 {signal}",
        "text": "\n".join(plain_lines)[:4900],
    }
