"""Gemini-powered explanation and news sentiment layer (free-tier friendly)."""
from __future__ import annotations

import json
import os
import time

import requests


def explain_analysis(payload: dict) -> dict:
    """Use Gemini only when GEMINI_API_KEY exists; otherwise keep rule-based scoring."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {
            "available": False,
            "news_score": None,
            "text": "尚未設定 GEMINI_API_KEY；目前只提供可重現的規則評分，消息面不納入分數。",
        }

    # Gemini 3.1 Flash-Lite is listed as a stable model with free-tier text tokens.
    # Quotas still apply; this code never switches to a paid model or billing tier.
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
    prompt = (
        "你是金融市場研究助理。只能根據 JSON 提供的數據與新聞做解讀，"
        "不可補造價格、財報、新聞或日期。請只回傳 JSON object，欄位為："
        "news_score（0 到 100 的整數，根據新聞整體可能利多/中性/利空評估；"
        "若沒有足夠新聞則為 null）、positive_factors（字串）、"
        "watch_items（字串）、risks（字串）、"
        "summary（繁體中文，包含日週線一致性與消息可能影響）。"
        "不可提供個人化買賣指令；資料不足要明說。資料如下：\\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model}:generateContent"
    )
    request_body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 700,
            "responseMimeType": "application/json",
        },
    }

    try:
        # A 503 is a temporary capacity issue, not evidence of a bad API key.
        # Retry transient server errors once with a short backoff; keep the
        # total wait bounded so a LINE webhook is less likely to time out.
        response = None
        for attempt in range(2):
            response = requests.post(
                url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=10,
            )
            if response.status_code in (500, 502, 503, 504) and attempt == 0:
                time.sleep(1)
                continue
            response.raise_for_status()
            break

        response_data = response.json()
        candidates = response_data.get("candidates") or []
        parts = (
            candidates[0].get("content", {}).get("parts", [])
            if candidates else []
        )
        raw = "".join(part.get("text", "") for part in parts).strip()
        if not raw:
            raise ValueError("Gemini 回傳空白內容")

        result = json.loads(raw)
        news_score = result.get("news_score")
        if news_score is not None:
            news_score = max(0.0, min(100.0, float(news_score)))

        text = (
            "正向因素：" + str(result.get("positive_factors", "未提供")) + "\\n"
            "觀察事項：" + str(result.get("watch_items", "未提供")) + "\\n"
            "主要風險：" + str(result.get("risks", "未提供")) + "\\n"
            "綜合解讀：" + str(result.get("summary", "未提供"))
        )
        return {"available": True, "news_score": news_score, "text": text}

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        if status == 429:
            message = "Gemini 免費額度或速率限制已達上限"
        elif status in (401, 403):
            message = "Gemini API 金鑰或專案權限無效"
        elif status == 404:
            message = f"找不到 Gemini 模型：{model}"
        elif status in (500, 502, 503, 504):
            message = f"Gemini 服務暫時過載（HTTP {status}），稍後再試"
        else:
            message = f"Gemini API HTTP {status}"
        return {
            "available": False,
            "news_score": None,
            "text": f"AI 解讀暫時不可用（{message}）；消息面不納入分數，請以原始資料為準。",
        }
    except (requests.RequestException, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "news_score": None,
            "text": f"AI 解讀暫時不可用（{type(exc).__name__}）；消息面不納入分數，請以原始資料為準。",
        }
