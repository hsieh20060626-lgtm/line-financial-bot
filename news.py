"""
川投顧｜台股財經新聞模組

負責「抓新聞」和「產生 LINE 卡片」，不依賴 LINE SDK，可以單獨測試。
實際傳送（回覆、推播）由 app.py 處理。

新聞來源：Google 新聞 RSS，用 site: 指定各家財經媒體，只取標題與連結。
"""

import os
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

TW_TZ = timezone(timedelta(hours=8))
COLOR_INFO = "#2C3E50"


# ==================================================
# 1. 新聞來源設定（想換媒體或關鍵字，改這裡就好）
# ==================================================

# (媒體名稱, Google 新聞搜尋語法)；when:1d 代表只找最近一天
NEWS_SOURCES = [
    ("鉅亨網", "site:news.cnyes.com 台股 when:1d"),
    ("經濟日報", "site:money.udn.com 台股 when:1d"),
    ("工商時報", "site:ctee.com.tw 台股 when:1d"),
    ("中央社", "site:cna.com.tw 台股 when:1d"),
]

PER_SOURCE = 2   # 每個媒體最多取幾則
TOTAL_LIMIT = 8  # 卡片最多顯示幾則
CACHE_SECONDS = 600


def _feed_url(query):
    return (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )


# ==================================================
# 2. 抓取新聞
# ==================================================

_cache = {"time": 0, "items": []}


def fetch_news():
    """回傳 [{'title': ..., 'link': ...}, ...]；單一來源失敗不影響其他來源。"""
    if _cache["items"] and time.time() - _cache["time"] < CACHE_SECONDS:
        return _cache["items"]

    items, seen = [], set()

    for source_name, query in NEWS_SOURCES:
        try:
            response = requests.get(
                _feed_url(query),
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:
            logger.warning("新聞來源讀取失敗（%s）：%s", source_name, exc)
            continue

        taken = 0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link.startswith("https://"):
                continue

            # 去掉標題尾端的「 - 媒體名稱」後再比對，避免同一則重複出現
            dedupe_key = title.rsplit(" - ", 1)[0]
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            items.append({"title": title, "link": link})
            taken += 1
            if taken >= PER_SOURCE:
                break

    items = items[:TOTAL_LIMIT]
    if items:
        _cache["time"] = time.time()
        _cache["items"] = items
    return items


# ==================================================
# 3. 產生卡片
# ==================================================

def news_card(items):
    """回傳 {'bubble': Flex 卡片, 'alt': 通知文字, 'text': 純文字備用版}。"""
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    contents = []

    for i, item in enumerate(items):
        if i:
            contents.append({"type": "separator", "margin": "md"})
        contents.append({
            "type": "text",
            "text": f"{i + 1}. {item['title'][:90]}",
            "size": "sm",
            "wrap": True,
            "margin": "md",
            "color": "#1A5276",
            "action": {"type": "uri", "label": "閱讀", "uri": item["link"]},
        })

    contents.append({
        "type": "text",
        "text": "點標題閱讀全文｜來源：Google 新聞（鉅亨網、經濟日報、工商時報、中央社）",
        "size": "xs",
        "color": "#888888",
        "align": "center",
        "margin": "lg",
        "wrap": True,
    })

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": COLOR_INFO,
            "paddingAll": "lg",
            "contents": [
                {"type": "text", "text": "📰 台股財經新聞", "color": "#FFFFFF", "weight": "bold", "size": "xl"},
                {"type": "text", "text": today, "color": "#FFFFFF", "size": "sm", "margin": "sm"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "contents": contents,
        },
    }

    plain = [f"📰 台股財經新聞 {today}", "━━━━━━━━━━━━━━"]
    for i, item in enumerate(items):
        plain.append(f"{i + 1}. {item['title']}\n{item['link']}")

    return {
        "bubble": bubble,
        "alt": f"📰 台股財經新聞 {today}",
        "text": "\n\n".join(plain),
    }


def query_news():
    """給機器人指令用：回傳卡片，抓不到就回傳提示文字。"""
    items = fetch_news()
    if not items:
        return "📰 新聞暫時無法取得，請稍後再試。"
    return news_card(items)


# ==================================================
# 4. 推播名單
# ==================================================

def news_recipients():
    """從環境變數 NEWS_USER_IDS 讀取收新聞的人（逗號分隔）。"""
    raw = os.getenv("NEWS_USER_IDS", "")
    return [uid.strip() for uid in raw.split(",") if uid.strip()]
