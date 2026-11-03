"""Optional OpenAI-compatible explanation layer. No model is called without an API key."""
from __future__ import annotations
import os
import requests


def explain_analysis(payload: dict) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"available": False, "text": "尚未設定 OPENAI_API_KEY；目前以下結論由可重現的規則評分產生，未呼叫生成式 AI。"}
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = (
        "你是金融市場研究助理。只能根據 JSON 中提供的資料解讀，不可補造價格、財報、新聞或日期。"
        "用繁體中文輸出：正向因素、觀察事項、主要風險、日週線一致性。若資料缺失必須明說。"
        "不提供個人化買賣指令。資料如下：\n" + __import__("json").dumps(payload, ensure_ascii=False, default=str)
    )
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 700},
            timeout=25,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        return {"available": bool(content), "text": content or "AI 未回傳可用內容。"}
    except Exception as exc:
        return {"available": False, "text": f"AI 解讀暫時不可用（{type(exc).__name__}）；請以規則評分與原始資料為準。"}
