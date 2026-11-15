"""Transparent, market-structure-aware scoring for swing analysis.

All component scores are relative evidence scores (0-100), not return
probabilities. Missing inputs stay missing; applicable weights are reported.
"""
from __future__ import annotations

import math

WEIGHTS = {
    "technical": 0.30,
    "fundamental": 0.20,  # replaced by ETF quality for funds
    "chips": 0.20,
    "market": 0.15,
    "news": 0.15,
}


def _clamp(value):
    return max(0.0, min(100.0, float(value)))


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _weighted(parts):
    usable = [(float(score), float(weight)) for score, weight in parts
              if score is not None and weight > 0]
    if not usable:
        return None
    return sum(score * weight for score, weight in usable) / sum(weight for _, weight in usable)


def _rsi_score(rsi):
    # RSI is momentum, not a standalone buy/sell trigger. Avoid rewarding
    # deeply oversold readings as if a reversal had already been confirmed.
    if rsi is None:
        return None
    points = [(0, 30), (20, 36), (30, 43), (40, 48), (50, 55),
              (60, 62), (65, 60), (70, 54), (80, 42), (100, 30)]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= rsi <= x1:
            return y0 + (rsi - x0) * (y1 - y0) / (x1 - x0)
    return 50.0


def _score_timeframe(item):
    if not item or not item.get("data_sufficient"):
        return None, []
    parts, reasons = [], []
    trend = item.get("trend")
    trend_score = {"bullish": 78, "bearish": 22, "mixed": 50,
                   "above_ma20": 62, "below_ma20": 38}.get(trend)
    if trend_score is not None:
        parts.append((trend_score, 0.40))
        reasons.append(f"{item.get('timeframe', '')}趨勢 {trend}")

    rsi = _finite(item.get("rsi14"))
    if rsi is not None:
        parts.append((_rsi_score(rsi), 0.20))
        reasons.append(f"RSI14={rsi:.1f}")

    hist = _finite(item.get("macd_histogram"))
    if hist is not None:
        direction = item.get("macd_histogram_direction")
        macd_score = 65 if hist > 0 and direction == "rising" else (
            58 if hist > 0 else 42 if hist < 0 and direction == "falling" else
            35 if hist < 0 else 50
        )
        parts.append((macd_score, 0.25))
        reasons.append("MACD動能增強" if hist > 0 and direction == "rising" else
                       "MACD動能轉弱" if hist < 0 and direction == "falling" else
                       "MACD柱狀體正值" if hist > 0 else
                       "MACD柱狀體負值" if hist < 0 else "MACD接近零軸")

    alignment = item.get("ma_alignment")
    if alignment in ("bullish", "bearish"):
        parts.append((72 if alignment == "bullish" else 28, 0.15))
        reasons.append(f"均線排列{('偏多' if alignment == 'bullish' else '偏空')}")
    return _weighted(parts), reasons


def score_technical(data):
    daily, weekly = data.get("daily", {}), data.get("weekly", {})
    daily_score, daily_reasons = _score_timeframe(daily)
    weekly_score, weekly_reasons = _score_timeframe(weekly)
    if daily_score is None and weekly_score is None:
        return None, ["日 K 與已收盤週 K 的有效資料不足，技術面不評分"]

    # Swing horizon: weekly structure has slightly more influence than daily noise.
    score = _weighted([(daily_score, 0.45), (weekly_score, 0.55)])
    reasons = daily_reasons + weekly_reasons
    comparison = data.get("timeframe_comparison", {})
    if comparison.get("conflicting"):
        score = 50 + (score - 50) * 0.70
        reasons.append("日 K 與週 K 趨勢相反，已向中性收斂")
    elif comparison.get("aligned"):
        score = 50 + (score - 50) * 1.08
        reasons.append("日 K 與週 K 趨勢一致，訊號略為增強")

    ratio = _finite(daily.get("volume_ratio"))
    if ratio is not None:
        # Volume confirms price direction; it does not create direction by itself.
        trend = daily.get("trend")
        if ratio >= 1.5 and trend == "bullish":
            score += 4
            reasons.append(f"上漲趨勢伴隨放量（量比 {ratio:.2f}）")
        elif ratio >= 1.5 and trend == "bearish":
            score -= 4
            reasons.append(f"下跌趨勢伴隨放量（量比 {ratio:.2f}）")
        elif ratio < 0.6:
            reasons.append(f"成交量偏低（量比 {ratio:.2f}），趨勢確認力較弱")
    return _clamp(score), reasons


def score_fundamental(data):
    if data and data.get("not_applicable"):
        return None, [data.get("reason") or "此標的不適用一般公司基本面評分"]
    if not data or not data.get("available"):
        return None, [((data or {}).get("reason") or "基本面資料缺失；不以 0 分計算")]

    parts, reasons = [], []
    eps = _finite(data.get("eps_ttm"))
    if eps is not None:
        parts.append((68 if eps > 0 else 22 if eps < 0 else 40, 0.20))
        reasons.append(f"近四季 EPS {eps:.2f}")

    growth = _finite(data.get("revenue_growth"))
    if growth is not None:
        parts.append((_clamp(50 + growth * 100), 0.20))
        reasons.append(f"營收年增率 {growth*100:.1f}%")

    earnings = _finite(data.get("earnings_growth"))
    if earnings is not None:
        parts.append((_clamp(50 + earnings * 100), 0.25))
        reasons.append(f"獲利年增率 {earnings*100:.1f}%")

    margin = _finite(data.get("profit_margin"))
    if margin is not None:
        parts.append((_clamp(45 + margin * 100), 0.15))
        reasons.append(f"獲利率 {margin*100:.1f}%")

    pe = _finite(data.get("trailing_pe"))
    if pe is not None and pe > 0:
        pe_score = 78 if pe <= 15 else 68 if pe <= 22 else 55 if pe <= 30 else 42 if pe <= 45 else 28
        parts.append((pe_score, 0.20))
        reasons.append(f"本益比 {pe:.1f} 倍（需搭配產業比較）")

    score = _weighted(parts)
    return (None, ["可用基本面欄位不足；資料缺失不以 0 分計算"]) if score is None else (_clamp(score), reasons)


def score_etf_quality(data):
    if not data or not data.get("is_etf"):
        return score_fundamental(data)
    parts, reasons = [], []
    expense = _finite(data.get("annual_expense_ratio"))
    if expense is not None and expense >= 0:
        # Annual expense ratio expressed as decimal (0.004 = 0.4%).
        s = 85 if expense <= 0.002 else 75 if expense <= 0.005 else 60 if expense <= 0.01 else 40 if expense <= 0.02 else 25
        parts.append((s, 0.30))
        reasons.append(f"年費用率 {expense*100:.3f}%")
    premium = _finite(data.get("premium_discount_pct"))
    if premium is not None:
        distance = abs(premium)
        s = 85 if distance <= 0.2 else 75 if distance <= 0.5 else 60 if distance <= 1 else 40 if distance <= 2 else 20
        parts.append((s, 0.30))
        reasons.append(f"市價相對 NAV 折溢價 {premium:+.2f}%")
    assets = _finite(data.get("total_assets"))
    if assets is not None and assets > 0:
        # Larger asset base generally supports fund continuity; not a guarantee of liquidity.
        s = 40 if assets < 1e8 else 55 if assets < 5e8 else 68 if assets < 2e9 else 78
        parts.append((s, 0.20))
        reasons.append(f"基金資產規模 {assets:,.0f}")
    volume = _finite(data.get("average_volume"))
    if volume is not None and volume > 0:
        s = 40 if volume < 100_000 else 55 if volume < 500_000 else 70 if volume < 2_000_000 else 80
        parts.append((s, 0.20))
        reasons.append(f"平均成交量 {volume:,.0f}")
    score = _weighted(parts)
    if score is None:
        return None, ["ETF 專屬資料不足（費用率、NAV 折溢價、規模、流動性均未取得）"]
    return _clamp(score), reasons + ["未取得的追蹤差異與持股集中度不納入評分"]


def score_chips(data):
    if data and data.get("not_applicable"):
        return None, [data.get("reason") or "此市場／標的不適用此籌碼指標"]
    if data and data.get("market") == "US" and data.get("method") == "volume_price_proxy":
        change, ratio = _finite(data.get("price_change_pct")), _finite(data.get("volume_ratio"))
        if change is None or ratio is None or ratio <= 0:
            return None, ["美股量價代理資料不足"]
        impact = max(-18.0, min(18.0, change * min(ratio, 2.0) * 3.0))
        return _clamp(50 + impact), [
            f"美股量價代理：漲跌 {change:+.2f}%，量比 {ratio:.2f}",
            "此分數不是機構持倉或法人買賣超"
        ]
    if not data or not data.get("available"):
        return None, [((data or {}).get("reason") or "籌碼資料缺失；不以 0 分計算")]
    volume = _finite(data.get("volume"))
    nets = [_finite(data.get(k)) for k in ("foreign_net", "trust_net", "dealer_net")]
    known = [v for v in nets if v is not None]
    if volume is None or volume <= 0 or not known:
        return None, ["缺少有效法人淨買賣超或成交量"]
    ratio = sum(known) / volume
    score = _clamp(50 + max(-30, min(30, ratio * 500)))
    return score, [f"最近交易日法人合計淨買賣超 {sum(known):,.0f} 股（占成交量 {ratio*100:.2f}%）"]


def score_market(data, market="TW"):
    if not data:
        return None, ["市場指數資料缺失"]
    if market == "TW":
        weights = {"TAIEX": 0.45, "SP500": 0.20, "NASDAQ": 0.15, "SOX": 0.20}
    else:
        weights = {"SP500": 0.45, "NASDAQ": 0.30, "SOX": 0.25}
    parts, reasons = [], []
    for name, weight in weights.items():
        row = data.get(name) or {}
        change = _finite(row.get("change_pct"))
        if change is None:
            continue
        # One-day returns are clipped to avoid a single shock dominating the score.
        component = _clamp(50 + max(-3.0, min(3.0, change)) * 8)
        parts.append((component, weight))
        reasons.append(f"{name} {change:+.2f}%")
    base = _weighted(parts)
    if base is None:
        return None, ["沒有可用的核心市場指數漲跌資料"]

    # VIX is a 30-day implied-volatility measure, not a directional equity index.
    # Use its level as a risk-regime overlay; never score a VIX rise as bullish.
    vix = _finite((data.get("VIX") or {}).get("close"))
    if vix is not None:
        penalty = 0 if vix < 18 else min(15.0, (vix - 18) * 0.75)
        base -= penalty
        reasons.append(f"VIX={vix:.2f}（波動風險調整 -{penalty:.1f} 分）")
    return _clamp(base), reasons


def score_news(items, sentiment_score=None):
    if not items:
        return None, ["未取得可驗證的近期新聞"]
    value = _finite(sentiment_score)
    if value is None:
        return None, ["未取得 AI 新聞情緒分數；消息面不納入總分"]
    return _clamp(value), [f"AI 依 {len(items)} 則近期新聞評估消息面"]


def calculate_market_score(technical, fundamental, chips, market, news,
                           news_score=None, market_type="TW"):
    fundamental_fn = score_etf_quality if fundamental and fundamental.get("is_etf") else score_fundamental
    details = {
        "technical": score_technical(technical),
        "fundamental": fundamental_fn(fundamental),
        "chips": score_chips(chips),
        "market": score_market(market, market_type),
        "news": score_news(news, news_score),
    }
    not_applicable = [
        key for key in details
        if (key == "fundamental" and fundamental and fundamental.get("not_applicable"))
        or (key == "chips" and chips and chips.get("not_applicable"))
    ]
    applicable_weight = sum(WEIGHTS[k] for k in details if k not in not_applicable)
    scored_weight = sum(WEIGHTS[k] for k, (v, _) in details.items() if v is not None)
    missing = [k for k, (v, _) in details.items() if v is None and k not in not_applicable]
    if not scored_weight:
        return {"score": None, "signal": "資料不足，停止評分", "dimensions": details,
                "data_completeness": 0.0, "applicable_weight": round(applicable_weight*100, 1),
                "scored_weight": 0.0, "not_applicable_dimensions": not_applicable,
                "missing_dimensions": missing, "effective_weights": {}}
    total = sum(WEIGHTS[k] * v for k, (v, _) in details.items() if v is not None) / scored_weight
    signal = ("強勢偏多" if total >= 80 else "偏多" if total >= 65 else
              "中性觀望" if total >= 45 else "偏空" if total >= 30 else "強勢偏空")
    return {
        "score": round(total, 1), "signal": signal, "dimensions": details,
        "data_completeness": round(scored_weight / applicable_weight * 100, 1) if applicable_weight else 100.0,
        "applicable_weight": round(applicable_weight*100, 1),
        "scored_weight": round(scored_weight*100, 1),
        "not_applicable_dimensions": not_applicable, "missing_dimensions": missing,
        "effective_weights": {k: round(WEIGHTS[k] / scored_weight, 4)
                              for k, (v, _) in details.items() if v is not None},
    }
