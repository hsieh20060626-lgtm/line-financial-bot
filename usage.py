"""
川投顧｜使用紀錄模組

把使用者輸入的指令轉成一句好讀的描述，例如：
    "2330"        -> "查詢 2330 股價"
    "籌碼 2330"   -> "查詢 2330 三大法人籌碼"

不依賴 LINE SDK，可以單獨測試。describe_command() 回傳 (描述, 是否值得通知管理員)。
"""

import re

PERIOD_WORDS = "當天|一天|一週|一個月|三個月|半年|一年|年初至今|今年|YTD"

MENU_WORDS = {
    "選單", "菜單", "menu", "help", "幫助", "說明", "功能",
    "功能選單", "完整選單", "使用說明", "hi", "hello", "start", "開始",
}

# 黃金、外匯、比特幣的顯示名稱
ASSET_LABELS = {
    "黃金": "黃金行情", "gold": "黃金行情",
    "比特幣": "比特幣行情", "btc": "比特幣行情", "bitcoin": "比特幣行情",
    "美元": "美元匯率", "美金": "美元匯率", "usd": "美元匯率",
    "日圓": "日圓匯率", "日元": "日圓匯率", "jpy": "日圓匯率",
    "歐元": "歐元匯率", "eur": "歐元匯率",
    "人民幣": "人民幣匯率", "cny": "人民幣匯率",
}


def _name(token):
    """資產用中文名稱，股票／ETF 用大寫代碼。"""
    return ASSET_LABELS.get(token.lower()) or token.upper()


def describe_command(text):
    t = (text or "").strip()
    low = t.lower()

    if not t:
        return "傳送空白訊息", False

    if low in MENU_WORDS:
        return "開啟功能選單", False

    if low in ("其他資訊", "其他", "other_info"):
        return "開啟其他資訊選單", False

    m = re.fullmatch(r"(法人|外資|投信)(買超|賣超)前五", t)
    if m:
        return f"查看{m.group(1)}{m.group(2)}前五名", True

    if low in ("買超前五", "買超排行"):
        return "查看法人買超前五名", True

    if low in ("賣超前五", "賣超排行"):
        return "查看法人賣超前五名", True

    if low in ("三大法人", "三大法人買賣超", "三大法人買超賣超", "法人動向"):
        return "查看三大法人全市場買賣超", True

    if low in ("籌碼", "法人"):
        return "點選籌碼功能", False

    if low in ("新聞", "財經新聞", "news"):
        return "查看財經新聞", True

    if low in ("我的id", "myid", "id"):
        return "查詢自己的 LINE ID", False

    if low in ASSET_LABELS:
        return f"查詢 {ASSET_LABELS[low]}", True

    m = re.fullmatch(r"自訂\s+(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})", t)
    if m:
        return f"查詢 {_name(m.group(1))} 自訂區間（{m.group(2)} 至 {m.group(3)}）", True

    m = re.fullmatch(r"自訂\s+(\S+)", t)
    if m:
        return f"點選 {_name(m.group(1))} 的自訂時間", False

    m = re.fullmatch(r"(?:籌碼|法人|三大法人)\s+(\S+)", t)
    if m:
        return f"查詢 {_name(m.group(1))} 三大法人籌碼", True

    m = re.fullmatch(r"外資\s+(\S+)", t)
    if m:
        return f"查詢 {_name(m.group(1))} 外資持股", True

    m = re.fullmatch(r"(?:本益比|估值|EPS)\s+(\S+)", t, flags=re.IGNORECASE)
    if m:
        return f"查詢 {_name(m.group(1))} 本益比與 EPS", True

    if t in ("本益比", "估值", "EPS", "eps"):
        return "查詢 2330 本益比與 EPS", True

    m = re.fullmatch(r"歷史\s+(\S+)", t)
    if m:
        return f"查詢 {_name(m.group(1))} 歷史股價（一個月）", True

    m = re.fullmatch(rf"(\S+)\s+({PERIOD_WORDS})", t, flags=re.IGNORECASE)
    if m:
        token, period = m.group(1), m.group(2)
        if token.lower() in ASSET_LABELS:
            return f"查詢 {_name(token)} {period}走勢", True
        return f"查詢 {_name(token)} {period}股價表現", True

    if re.fullmatch(r"[A-Za-z0-9.^=_-]{1,20}", t):
        if low in ASSET_LABELS:
            return f"查詢 {ASSET_LABELS[low]}", True
        return f"查詢 {t.upper()} 股價", True

    return f"傳送「{t[:30]}」（無法辨識的指令）", False
