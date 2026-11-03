"""Transparent weighted scoring for swing analysis; missing dimensions are reweighted."""
from __future__ import annotations
import math

WEIGHTS = {"technical": 0.30, "fundamental": 0.20, "chips": 0.20, "market": 0.15, "news": 0.15}


def _clamp(value):
    return max(0.0, min(100.0, float(value)))


def score_technical(data):
    daily, weekly = data.get("daily", {}), data.get("weekly", {})
    if not daily or not weekly or not daily.get("data_sufficient") or not weekly.get("data_sufficient"):
        return None, ["日 K 或週 K 歷史長度不足，技術面不評分"]
    score, reasons = 50.0, []
    for item, weight in ((daily, 0.65), (weekly, 0.35)):
        trend = item.get("trend")
        if trend == "bullish":
            score += 18 * weight
            reasons.append(f"{item['timeframe']}趨勢偏多")
        elif trend == "bearish":
            score -= 18 * weight
            reasons.append(f"{item['timeframe']}趨勢偏空")
        if item.get("rsi14") is not None:
            rsi = item["rsi14"]
            score += (8 if 45 <= rsi <= 65 else -8 if rsi >= 75 or rsi <= 25 else 0) * weight
            reasons.append(f"{item['timeframe']} RSI={rsi:.1f}")
        hist = item.get("macd_histogram")
        if hist is not None:
            score += (7 if hist > 0 else -7 if hist < 0 else 0) * weight
        if item.get("ma_alignment") == "bullish":
            score += 7 * weight
        elif item.get("ma_alignment") == "bearish":
            score -= 7 * weight
    comparison = data.get("timeframe_comparison", {})
    if comparison.get("conflicting"):
        score = 50 + (score - 50) * 0.65
        reasons.append("日 K 與週 K 方向矛盾，已降低訊號強度")
    return _clamp(score), reasons


def score_fundamental(data):
    if not data or not data.get("available"):
        return None, ["基本面資料不足或標的不適用"]
    score, used, reasons = 50.0, 0, []
    growth = data.get("revenue_growth")
    earnings = data.get("earnings_growth")
    margin = data.get("profit_margin")
    pe = data.get("trailing_pe")
    if growth is not None:
        score += max(-12, min(12, growth * 30)); used += 1
        reasons.append(f"營收成長 {growth*100:.1f}%")
    if earnings is not None:
        score += max(-12, min(12, earnings * 20)); used += 1
        reasons.append(f"獲利成長 {earnings*100:.1f}%")
    if margin is not None:
        score += max(-8, min(8, margin * 20)); used += 1
    if pe is not None and pe > 0:
        score += 4 if pe < 20 else 0 if pe < 35 else -5 if pe < 60 else -10
        used += 1; reasons.append(f"本益比 {pe:.1f} 倍")
    if not used:
        return None, ["可用基本面欄位不足"]
    return _clamp(score), reasons


def score_chips(data):
    if not data or not data.get("available"):
        return None, ["籌碼資料缺失；美股不套用台股三大法人資料"]
    nets = [data.get(k) for k in ("foreign_net", "trust_net", "dealer_net")]
    known = [v for v in nets if v is not None]
    if not known:
        return None, ["無有效法人買賣超資料"]
    total = sum(known)
    volume = data.get("volume")
    try:
        volume = float(volume)
        if not math.isfinite(volume) or volume <= 0:
            volume = None
    except (TypeError, ValueError):
        volume = None
    if volume is None:
        return None, ["缺少有效成交量，無法標準化法人買賣超"]
    score = _clamp(50 + max(-25, min(25, total / volume * 250)))
    return score, [f"最近交易日法人合計淨買賣超 {total:,.0f} 股"]


def score_market(data):
    if not data:
        return None, ["市場指數資料缺失"]
    values = [v.get("change_pct") for v in data.values() if v.get("change_pct") is not None]
    if not values:
        return None, ["沒有可用的市場指數漲跌資料"]
    avg = sum(values) / len(values)
    return _clamp(50 + avg * 4), [f"相關指數平均漲跌 {avg:+.2f}%"]


def score_news(items, sentiment_score=None):
    if not items:
        return None, ["未取得可驗證的近期新聞"]
    if sentiment_score is None:
        return None, ["未取得 AI 新聞情緒分數；消息面不納入總分"]
    return _clamp(sentiment_score), [f"AI 依 {len(items)} 則近期新聞評估消息面"]


def calculate_market_score(technical, fundamental, chips, market, news, news_score=None):
    details = {
        "technical": score_technical(technical),
        "fundamental": score_fundamental(fundamental),
        "chips": score_chips(chips),
        "market": score_market(market),
        "news": score_news(news, news_score),
    }
    available_weight = sum(WEIGHTS[k] for k, (score, _) in details.items() if score is not None)
    if not available_weight:
        return {"score": None, "signal": "資料不足，停止評分", "dimensions": details,
                "data_completeness": 0.0, "effective_weights": {}}
    total = sum(WEIGHTS[k] * score for k, (score, _) in details.items() if score is not None) / available_weight
    signal = ("強勢偏多" if total >= 80 else "偏多" if total >= 65 else
              "中性觀望" if total >= 45 else "偏空" if total >= 30 else "強勢偏空")
    effective = {k: round(WEIGHTS[k] / available_weight, 4) for k, (v, _) in details.items() if v is not None}
    return {"score": round(total, 1), "signal": signal, "dimensions": details,
            "data_completeness": round(available_weight * 100, 1), "effective_weights": effective}
