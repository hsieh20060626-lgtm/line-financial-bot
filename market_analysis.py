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
    history = market_data["history"]
    technical = analyze_technical_indicators(history)
    fundamental = fetch_fundamentals(ticker, market)

    if market == "TW":
        chips = fetch_stock_chips(user_symbol, os.getenv("FINMIND_TOKEN", ""))
        if chips.get("available"):
            chips["volume"] = float(history["Volume"].iloc[-1] or 0)
            chips["market"] = "TW"
    else:
        # US equities do not have a directly comparable, daily public feed for
        # Taiwan-style three-institution net trading. Use a clearly labelled
        # OHLCV price/volume proxy; never describe it as institutional flow.
        daily = technical.get("daily", {})
        volume_ratio = daily.get("volume_ratio")
        try:
            last_close = float(history["Close"].iloc[-1])
            prior_close = float(history["Close"].iloc[-2])
            price_change_pct = (last_close / prior_close - 1) * 100 if prior_close else None
        except (IndexError, TypeError, ValueError, ZeroDivisionError):
            price_change_pct = None
        chips = {
            "available": volume_ratio is not None and price_change_pct is not None,
            "market": "US",
            "method": "volume_price_proxy",
            "volume_ratio": volume_ratio,
            "price_change_pct": price_change_pct,
            "date": market_data["as_of"],
            "source": "Yahoo Finance OHLCV",
            "reason": "美股無台股式每日三大法人買賣超公開資料；本版改用價格與成交量代理指標，並非機構持倉資料",
        }

    benchmarks = fetch_benchmarks(market)
    news_items = fetch_symbol_news(user_symbol) + fetch_macro_news()
    score = calculate_market_score(technical, fundamental, chips, benchmarks, news_items)
    payload = {
        "symbol": user_symbol.upper(), "resolved_symbol": ticker, "market": market,
        "as_of": market_data["as_of"], "source": market_data["source"],
        "stale": market_data["stale"], "generated_at": datetime.now(timezone.utc).isoformat(),
        "technical": technical, "fundamental": fundamental, "chips": chips,
        "benchmarks": benchmarks, "news": news_items, "score": score,
    }
    payload["ai"] = explain_analysis(payload)
    payload["score"] = calculate_market_score(
        technical, fundamental, chips, benchmarks, news_items,
        news_score=payload["ai"].get("news_score")
    )
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


def _recommendation_score_component(scores: dict) -> dict:
    """將三種操作方向的相對支持度並排顯示。"""
    columns = []
    for name, value in scores.items():
        columns.append({
            "type": "box", "layout": "vertical", "flex": 1,
            "backgroundColor": "#F3F6F8", "cornerRadius": "md",
            "paddingAll": "sm", "spacing": "xs",
            "contents": [
                _text_component(name, "xs", "#566573", "bold", align="center"),
                _text_component(f"{value} 分", "md", "#1F618D", "bold", align="center"),
            ],
        })
    return {
        "type": "box", "layout": "horizontal", "spacing": "sm",
        "contents": columns,
    }


def _section_title(text: str) -> dict:
    return {
        "type": "text", "text": text, "size": "md", "weight": "bold",
        "color": "#2C3E50", "margin": "lg", "wrap": True,
    }


def recommendation_for_position(score: dict, position: str) -> dict:
    """依總分與資料完整度，產生持有／未持有情境的相對支持度（非獲利機率）。"""
    total = score.get("score")
    completeness = float(score.get("data_completeness") or 0)
    if total is None or completeness < 40:
        return {
            "position": position, "action": "資料不足，暫不提供操作方向",
            "scores": {}, "reason": "有效資料覆蓋率低於 40%，避免依不足資料給出操作方向",
        }

    total = max(0.0, min(100.0, float(total)))
    if position == "held":
        names = ("加碼／買進", "持有", "減碼／賣出")
        raw = [
            max(total - 60.0, 0.0),
            max(30.0 - abs(total - 50.0), 0.0),
            max(45.0 - total, 0.0),
        ]
        action = "偏向加碼／買進" if total >= 65 else (
            "偏向持有" if total >= 45 else "偏向減碼／賣出"
        )
    else:
        names = ("買進", "等待觀望", "暫不買進")
        raw = [
            max(total - 40.0, 0.0),
            max(30.0 - abs(total - 55.0), 0.0),
            max(40.0 - total, 0.0),
        ]
        action = "偏向買進" if total >= 65 else (
            "偏向等待觀望" if total >= 45 else "暫不建議進場"
        )

    denominator = sum(raw)
    if denominator <= 0:
        values = [0, 100, 0]
    else:
        values = [round(value / denominator * 100) for value in raw]
        # 修正四捨五入差額，確保三項合計 100。
        values[values.index(max(values))] += 100 - sum(values)

    return {
        "position": position, "action": action,
        "scores": dict(zip(names, values)),
        "reason": "分數為依市場總分轉換的相對支持度，不代表獲利機率或保證報酬",
    }


def format_market_analysis(data: dict, position: str | None = None) -> dict:
    """回傳 LINE Flex Message 卡片；五大面向固定使用同一張三欄橫向表格。"""
    score = data["score"]
    symbol = data["symbol"]
    market_name = "台股" if data["market"] == "TW" else "美股"
    signal = score.get("signal") or "資料不足"
    total = "資料不足" if score.get("score") is None else f'{score["score"]:.1f}/100'
    completeness = f'{score["data_completeness"]:.0f}%'
    applicable_weight = f'{score.get("applicable_weight", 100):.0f}%'
    recommendation = recommendation_for_position(score, position) if position in ("held", "unheld") else None
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
        score_text = "不適用" if key in score.get("not_applicable_dimensions", []) else (
            "未評分" if value is None else f"{value:.1f}"
        )
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
        *(
            [
                _section_title("操作建議評分｜已經持有"),
                _text_component("主要判斷： " + recommendation["action"], "md", "#2C3E50", "bold"),
                _recommendation_score_component(recommendation["scores"]),
                _text_component(recommendation["reason"], "xs", "#777777"),
            ] if recommendation and recommendation["position"] == "held" and recommendation["scores"] else
            [
                _section_title("操作建議評分｜尚未持有"),
                _text_component("主要判斷： " + recommendation["action"], "md", "#2C3E50", "bold"),
                *[_text_component(f"{name}：{value} 分", "sm") for name, value in recommendation["scores"].items()],
                _text_component(recommendation["reason"], "xs", "#777777"),
            ] if recommendation and recommendation["scores"] else
            ([
                _section_title("操作建議評分"),
                _text_component(recommendation["action"] + "：" + recommendation["reason"], "sm", "#B9770E"),
            ] if recommendation else [])
        ),
        _text_component(
            f"資料覆蓋率：{completeness}（排除不適用項目；適用權重 {applicable_weight}）",
            "xs", "#777777"
        ),
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

    body.append(_section_title("籌碼／量價資料"))
    if market_name == "美股":
        body.append(_text_component(
            chips.get("reason", "美股使用量價代理指標，不代表機構持倉或法人買賣超。"),
            "sm"
        ))
        if chips.get("available"):
            body.append(_text_component(
                f'資料日期：{chips.get("date")}｜漲跌：{chips.get("price_change_pct"):+.2f}%｜成交量／20 日均量：{chips.get("volume_ratio"):.2f} 倍',
                "sm"
            ))
    elif chips.get("available"):
        body.append(_text_component(f'資料日期：{chips.get("date")}（單位：股）', "sm"))
    else:
        body.append(_text_component(chips.get("reason", "目前無可用資料"), "sm"))

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

    plain_lines = [
        f"📊 川投顧市場訊號分析｜{symbol}",
        f"市場：{market_name}｜資料日期：{data.get('as_of', '未提供')}",
        f"總分：{total}｜訊號：{signal}",
        *(
            [f"操作建議（已經持有）：{recommendation['action']}"] +
            ["｜".join(f"{name}：{value} 分" for name, value in recommendation["scores"].items())] +
            [recommendation["reason"]]
            if recommendation and recommendation["position"] == "held" else
            [f"操作建議（尚未持有）：{recommendation['action']}"] +
            [f"{name}：{value} 分" for name, value in recommendation["scores"].items()] +
            [recommendation["reason"]]
            if recommendation else []
        ),
        f"資料覆蓋率：{completeness}（排除不適用項目；適用權重 {applicable_weight}）",
        "分析項目｜分數｜分析結果",
    ]
    for key, label in labels.items():
        value, reasons = score["dimensions"][key]
        score_text = "不適用" if key in score.get("not_applicable_dimensions", []) else (
            "未評分" if value is None else f"{value:.1f}"
        )
        plain_lines.append(f"{label} {weights[key]}｜{score_text}｜{'；'.join(reasons[:2]) if reasons else '目前沒有可用的分析依據'}")
    plain_lines += [
        f'日週線：日 K {daily.get("trend", "未提供")}；週 K {weekly.get("trend", "未提供")}',
        "AI 解讀：" + ai_text,
        "※ 美股籌碼面使用量價代理指標，不代表機構持倉或即時法人買賣超。",
        "※ 本分析為規則化研究輔助，不構成投資建議；評分未經歷史回測，不代表未來報酬。",
    ]
    return {
        "bubble": bubble,
        "alt": f"川投顧市場訊號分析 {symbol}，總分 {total}，訊號 {signal}",
        "text": "\n".join(plain_lines)[:4900],
    }
