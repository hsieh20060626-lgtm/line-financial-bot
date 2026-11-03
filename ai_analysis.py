"""Optional OpenAI-compatible explanation and news sentiment layer."""
from __future__ import annotations
import json
import os
import requests


def explain_analysis(payload: dict) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"available": False, "news_score": None,
                "text": "尚未確認已設定 OPENAI_API_KEY；目前只提供可重現的規則評分，消息面不納入分數。"}
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = (
        "你是金融市場研究助理。只能根據 JSON 提供的數據與新聞做解讀，不可補造價格、財報、新聞或日期。"
        "請回傳 JSON object，欄位為 news_score（0 到 100 的整數，根據新聞整體可能利多/中性/利空評估；"
        "若沒有足夠新聞則為 null）、positive_factors（字串）、watch_items（字串）、risks（字串）、"
        "summary（繁體中文，包含日週線一致性與消息可能影響）。"
        "不可提供個人化買賣指令；資料不足要明說。資料如下：\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 700,
                  "response_format": {"type": "json_object"}},
            timeout=8,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        result = json.loads(raw)
        news_score = result.get("news_score")
        if news_score is not None:
            news_score = max(0.0, min(100.0, float(news_score)))
        text = (
            "正向因素：" + str(result.get("positive_factors", "未提供")) + "\n"
            "觀察事項：" + str(result.get("watch_items", "未提供")) + "\n"
            "主要風險：" + str(result.get("risks", "未提供")) + "\n"
            "綜合解讀：" + str(result.get("summary", "未提供"))
        )
        return {"available": True, "news_score": news_score, "text": text}
    except Exception as exc:
        return {"available": False, "news_score": None,
                "text": f"AI 解讀暫時不可用（{type(exc).__name__}）；消息面不納入分數，請以原始資料為準。"}
