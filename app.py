import os
import re
import time
import hmac
import logging
from datetime import date, timedelta

import requests
import yfinance as yf
from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    MulticastRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from news import fetch_news, news_card, query_news, news_recipients


# ==================================================
# 1. 基本設定
# ==================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def require_env(name):
    """金鑰只從環境變數讀取，沒設定就直接報錯，避免誤用或外洩。"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少環境變數：{name}")
    return value


LINE_CHANNEL_ACCESS_TOKEN = require_env("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = require_env("LINE_CHANNEL_SECRET")
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
# 新聞推播：CRON_SECRET 是排程呼叫 /push_news 時的密碼；NEWS_USER_IDS 是收新聞的人（逗號分隔）
CRON_SECRET = os.getenv("CRON_SECRET", "")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DISCLAIMER = "※ 資料僅供參考，非投資建議。"

# 台灣習慣：紅色＝上漲／買超，綠色＝下跌／賣超
COLOR_UP = "#C0392B"
COLOR_DOWN = "#1E8449"
COLOR_FLAT = "#7F8C8D"
COLOR_INFO = "#2C3E50"


# ==================================================
# 2. 共用工具函式
# ==================================================

# ---- 簡易快取：減少 yfinance 被限流、加快回覆速度 ----
_cache = {}


def cache_get(key, ttl):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def cache_set(key, value):
    if len(_cache) > 500:  # 避免無限成長
        _cache.clear()
    _cache[key] = (time.time(), value)


def fetch_history(symbol, ttl=300, **kwargs):
    """取得歷史價格（已去除沒有收盤價的列），有資料才快取。"""
    key = ("hist", symbol, tuple(sorted(kwargs.items())))
    hit = cache_get(key, ttl)
    if hit is not None:
        return hit

    hist = yf.Ticker(symbol).history(auto_adjust=False, **kwargs)
    if hist.empty or "Close" not in hist.columns:
        return hist.iloc[0:0]
    hist = hist.dropna(subset=["Close"])
    if not hist.empty:
        cache_set(key, hist)
    return hist


def fetch_asset_history(symbol, **kwargs):
    """同 fetch_history；日圓若直接查不到，改用 USD/TWD ÷ USD/JPY 推算。"""
    hist = fetch_history(symbol, **kwargs)
    if hist.empty and symbol == "JPYTWD=X":
        usd_twd = fetch_history("USDTWD=X", **kwargs)
        usd_jpy = fetch_history("JPY=X", **kwargs)
        if not usd_twd.empty and not usd_jpy.empty:
            cross = (usd_twd["Close"] / usd_jpy["Close"]).dropna()
            if not cross.empty:
                hist = cross.to_frame("Close")
    return hist


def get_info(symbol):
    """取得公司資訊（yfinance 的 info 很慢，因此快取 1 小時）。"""
    key = ("info", symbol)
    hit = cache_get(key, 3600)
    if hit is not None:
        return hit
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}
    if info:
        cache_set(key, info)
    return info


def get_quarterly(symbol):
    """取得季度損益表（快取 6 小時）。"""
    key = ("quarterly", symbol)
    hit = cache_get(key, 6 * 3600)
    if hit is not None:
        return hit
    try:
        data = yf.Ticker(symbol).quarterly_income_stmt
    except Exception as exc:
        logger.warning("取得季度財報失敗 (%s)：%s", symbol, exc)
        return None
    if data is not None and not data.empty:
        cache_set(key, data)
    return data


# ---- 台股代碼（含 ETF，例如 2330、0050、00981A）----
TW_CODE_RE = re.compile(r"\d{4,6}[A-Z]?")


def is_tw_code(symbol):
    return bool(TW_CODE_RE.fullmatch(symbol.strip().upper()))


_resolved = {}


def normalize_ticker(symbol):
    """
    台股（含 ETF）代碼自動補上 Yahoo Finance 後綴，使用者不用自己加 .TW。
    上市用 .TW、上櫃用 .TWO：先試 .TW，沒資料再試 .TWO。
    """
    symbol = symbol.strip().upper()
    if not is_tw_code(symbol):
        return symbol

    if symbol in _resolved:
        return _resolved[symbol]

    for suffix in (".TW", ".TWO"):
        candidate = symbol + suffix
        try:
            if not fetch_history(candidate, period="5d").empty:
                _resolved[symbol] = candidate
                return candidate
        except Exception:
            continue

    return symbol + ".TW"  # 都查不到時沿用原本行為，由後續流程回報查無資料


def to_number(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_latest_row(data):
    """從 FinMind 回傳的陣列中取出日期最新的一筆資料。"""
    if not data:
        return None
    valid_rows = [row for row in data if isinstance(row, dict) and row.get("date")]
    if not valid_rows:
        return None
    return max(valid_rows, key=lambda row: str(row["date"]))


def get_finmind_data(dataset, stock_id=None, start_date=None):
    params = {"dataset": dataset}
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN
    if stock_id:
        params["data_id"] = stock_id
    if start_date:
        params["start_date"] = start_date

    key = ("finmind", tuple(sorted(params.items())))
    hit = cache_get(key, 300)
    if hit is not None:
        return hit

    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        data = result.get("data", [])
        if data:
            cache_set(key, data)
        return data
    except Exception as exc:
        logger.warning("FinMind 查詢失敗 (%s)：%s", dataset, exc)
        return []


def get_tw_stock_name(code):
    """用 FinMind 查台股／ETF 的中文名稱（快取 24 小時），查不到回傳 None。"""
    code = code.strip().upper()
    key = ("twname", code)
    hit = cache_get(key, 86400)
    if hit:
        return hit
    for row in get_finmind_data("TaiwanStockInfo", stock_id=code):
        name = row.get("stock_name") if isinstance(row, dict) else None
        if name:
            cache_set(key, name)
            return name
    return None


def stock_meta(upper_code, symbol):
    """回傳 (名稱, 幣別)。"""
    if is_tw_code(upper_code):
        name = get_tw_stock_name(upper_code)
        if not name:
            info = get_info(symbol)
            name = info.get("shortName") or info.get("longName") or upper_code
        return name, "TWD"
    info = get_info(symbol)
    name = info.get("shortName") or info.get("longName") or upper_code
    return name, info.get("currency", "")


def stock_display(upper_code, symbol=None):
    """標題用的「名稱（代碼）」。"""
    symbol = symbol or normalize_ticker(upper_code)
    name, _ = stock_meta(upper_code, symbol)
    return f"{name}（{upper_code}）" if name != upper_code else upper_code


# ==================================================
# 3. LINE 訊息與快速選單
# ==================================================

def build_quick(items):
    """items：[(按鈕文字, 送出的指令), ...]"""
    return [
        QuickReplyItem(action=MessageAction(label=label[:20], text=command))
        for label, command in items
    ]


def quick_reply_items():
    """聊天室底部預設快速選單。"""
    return build_quick([
        ("完整選單", "選單"),
        ("即時股價", "2330"),
        ("法人籌碼", "籌碼 2330"),
        ("本益比/EPS", "本益比 2330"),
        ("歷史股價", "歷史 2330"),
        ("財經新聞", "新聞"),
        ("其他資訊", "other_info"),
    ])


def period_quick(alias, include_today=False):
    """查詢股價／匯率後的時間單位選單。"""
    items = []
    if include_today:
        items.append(("今日漲跌", alias))
    items += [
        ("一週", f"{alias} 一週"),
        ("一個月", f"{alias} 一個月"),
        ("三個月", f"{alias} 三個月"),
        ("年初至今", f"{alias} 年初至今"),
        ("自訂時間", f"自訂 {alias}"),
        ("完整選單", "選單"),
    ]
    return build_quick(items)


def build_message(content, quick_replies=None):
    """content 可以是純文字，或 make_card() 產生的卡片。"""
    quick = QuickReply(items=quick_replies) if quick_replies else None

    if isinstance(content, dict):
        try:
            return FlexMessage(
                alt_text=str(content["alt"])[:400],
                contents=FlexContainer.from_dict(content["bubble"]),
                quick_reply=quick,
            )
        except Exception:
            logger.exception("卡片建立失敗，改用純文字")
            content = content.get("text", "查詢完成，但顯示卡片時發生問題。")

    return TextMessage(text=str(content)[:4900], quick_reply=quick)


def send_reply(reply_token, content, quick_replies=None):
    message = build_message(content, quick_replies)

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[message],
            )
        )


# ==================================================
# 4. 表格卡片（Flex Message）
# ==================================================

SEP = "SEP"


def R(label, value, big=False, color=None):
    """卡片中的一列：左邊標題、右邊數值。"""
    return {"label": label, "value": str(value), "big": big, "color": color}


def trend_color(value):
    if value is None:
        return COLOR_FLAT
    if value > 0:
        return COLOR_UP
    if value < 0:
        return COLOR_DOWN
    return COLOR_FLAT


def make_card(title, subtitle, color, rows, footer=None):
    """產生像表格一樣的卡片：彩色標題、中間資料列、底部小字。"""
    footer = footer or []
    contents = []

    for row in rows:
        if row == SEP:
            contents.append({"type": "separator", "margin": "lg"})
            continue

        big = row["big"]
        contents.append({
            "type": "box",
            "layout": "horizontal",
            "margin": "lg" if big else "md",
            "contents": [
                {
                    "type": "text",
                    "text": row["label"],
                    "size": "lg" if big else "md",
                    "weight": "bold" if big else "regular",
                    "color": "#555555",
                    "flex": 4,
                    "gravity": "center",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": row["value"],
                    "size": "xl" if big else "md",
                    "weight": "bold",
                    "color": row["color"] or "#222222",
                    "align": "end",
                    "flex": 5,
                    "gravity": "center",
                    "wrap": True,
                },
            ],
        })

    if footer:
        contents.append({"type": "separator", "margin": "lg", "color": "#FFFFFF"})
        for line in footer:
            contents.append({
                "type": "text",
                "text": line,
                "size": "xs",
                "color": "#888888",
                "align": "center",
                "margin": "sm",
                "wrap": True,
            })

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": color,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "xl",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": subtitle,
                    "color": "#FFFFFF",
                    "size": "sm",
                    "margin": "sm",
                    "wrap": True,
                },
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "contents": contents,
        },
    }

    # 純文字版本：Flex 顯示失敗時備用
    plain_lines = [title, subtitle, "━━━━━━━━━━━━━━"]
    plain_lines += [f"{r['label']}：{r['value']}" for r in rows if r != SEP]
    plain_lines += footer

    return {
        "bubble": bubble,
        "alt": f"{title}｜{subtitle}",
        "text": "\n".join(plain_lines),
    }


# ==================================================
# 5. 查詢標的：股票／ETF 與 黃金、外匯、比特幣
# ==================================================

ASSETS = {
    "USD": {"name": "美元", "symbol": "USDTWD=X", "pair": "USD/TWD", "kind": "fx", "icon": "💱", "decimals": 4, "currency": ""},
    "JPY": {"name": "日圓", "symbol": "JPYTWD=X", "pair": "JPY/TWD", "kind": "fx", "icon": "💱", "decimals": 4, "currency": ""},
    "EUR": {"name": "歐元", "symbol": "EURTWD=X", "pair": "EUR/TWD", "kind": "fx", "icon": "💱", "decimals": 4, "currency": ""},
    "CNY": {"name": "人民幣", "symbol": "CNYTWD=X", "pair": "CNY/TWD", "kind": "fx", "icon": "💱", "decimals": 4, "currency": ""},
    "GOLD": {"name": "黃金", "symbol": "GC=F", "pair": "美元／盎司", "kind": "commodity", "icon": "🥇", "decimals": 2, "currency": "USD"},
    "BTC": {"name": "比特幣", "symbol": "BTC-USD", "pair": "BTC/USD", "kind": "crypto", "icon": "₿", "decimals": 2, "currency": "USD"},
}

ASSET_ALIASES = {
    "美元": "USD", "美金": "USD", "usd": "USD",
    "日圓": "JPY", "日元": "JPY", "jpy": "JPY",
    "歐元": "EUR", "eur": "EUR",
    "人民幣": "CNY", "cny": "CNY",
    "黃金": "GOLD", "gold": "GOLD",
    "比特幣": "BTC", "btc": "BTC", "bitcoin": "BTC",
}


def alias_for(token):
    """快速選單按鈕使用的名稱：資產用中文名，股票用大寫代碼。"""
    code = ASSET_ALIASES.get(token.strip().lower())
    if code:
        return ASSETS[code]["name"]
    return token.strip().upper()


def resolve_target(token):
    """把使用者輸入轉成查詢資訊（代碼、標題、幣別、小數位數…）。"""
    token = token.strip()
    code = ASSET_ALIASES.get(token.lower())

    if code:
        a = ASSETS[code]
        return {
            "is_asset": True,
            "kind": a["kind"],
            "symbol": a["symbol"],
            "display": f"{a['icon']} {a['name']}（{a['pair']}）",
            "currency": a["currency"],
            "decimals": a["decimals"],
            "code": a["pair"],
        }

    upper = token.upper()
    symbol = normalize_ticker(upper)
    name, currency = stock_meta(upper, symbol)
    return {
        "is_asset": False,
        "kind": "stock",
        "symbol": symbol,
        "display": f"{name}（{upper}）" if name != upper else upper,
        "currency": currency,
        "decimals": 2,
        "code": upper,
    }


def card_title(target, change=None):
    if target["is_asset"]:
        return target["display"]
    icon = "📉" if (change is not None and change < 0) else "📈"
    return f"{icon} {target['display']}"


def quote_card(target, hist):
    """今日漲跌卡片。"""
    d = target["decimals"]
    is_fx = target["kind"] == "fx"
    cur = f" {target['currency']}" if target["currency"] else ""

    price = float(hist.iloc[-1]["Close"])
    previous = float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else price
    change = price - previous
    change_pct = (change / previous * 100) if previous else 0
    color = trend_color(change)

    rows = [
        R("最新匯率" if is_fx else "最新價格", f"{price:,.{d}f}{cur}"),
        R("前一日匯率" if is_fx else "前一交易日收盤", f"{previous:,.{d}f}{cur}"),
        SEP,
        R("今日漲跌幅", f"{change_pct:+.2f}%", big=True, color=color),
        R("漲跌金額", f"{change:+,.{d}f}", color=color),
    ]

    footer = [
        f"資料日期：{hist.index[-1].strftime('%Y-%m-%d')}",
        f"資料代碼：{target['symbol']}",
    ]
    if is_fx:
        footer.insert(0, f"1 單位外幣 ≈ {price:.4f} 新台幣")

    return make_card(card_title(target, change), "今日漲跌幅", color, rows, footer)


def period_card(target, hist, subtitle):
    """區間漲跌卡片。"""
    d = target["decimals"]
    is_fx = target["kind"] == "fx"

    first_price = float(hist.iloc[0]["Close"])
    last_price = float(hist.iloc[-1]["Close"])
    change = last_price - first_price
    change_pct = (change / first_price * 100) if first_price else 0
    color = trend_color(change)

    rows = [
        R("起始匯率" if is_fx else "起始參考價", f"{first_price:,.{d}f}"),
        R("最新匯率" if is_fx else "最新收盤價", f"{last_price:,.{d}f}"),
        SEP,
        R("區間漲跌幅", f"{change_pct:+.2f}%", big=True, color=color),
        R("漲跌金額", f"{change:+,.{d}f}", color=color),
    ]

    footer = [
        f"起始日期：{hist.index[0].strftime('%Y-%m-%d')}",
        f"資料更新日：{hist.index[-1].strftime('%Y-%m-%d')}",
        f"資料代碼：{target['symbol']}",
    ]

    return make_card(card_title(target, change), subtitle, color, rows, footer)


# ==================================================
# 6. 完整功能選單／幫助說明
# ==================================================

def help_text():
    return (
        "📊 川投顧｜完整功能選單\n"
        "━━━━━━━━━━━━━━\n"
        "📈【一、即時股價】\n"
        "直接輸入股票或 ETF 代碼\n"
        "（例：2330、0050、00981A、AAPL）\n"
        "查詢後會先顯示今日漲跌幅，\n"
        "下方按鈕可切換一週、一個月、\n"
        "三個月、年初至今、自訂時間\n\n"
        "📉【二、歷史股價與漲跌】\n"
        "歷史 2330\n"
        "2330 一個月\n"
        "自訂 2330 2026-01-01 2026-10-01\n\n"
        "🏦【三、三大法人與籌碼】\n"
        "籌碼 2330\n"
        "外資 2330\n\n"
        "🧮【四、本益比與 EPS】\n"
        "本益比 2330\n"
        "本益比 AAPL\n\n"
        "🥇【五、黃金】\n"
        "黃金\n\n"
        "💱【六、外匯匯率】\n"
        "美元、日圓、歐元、人民幣\n"
        "（例：美元 一週）\n\n"
        "₿【七、比特幣】\n"
        "比特幣 / BTC\n\n"
        "📰【八、財經新聞】\n"
        "新聞（每天早上也會自動推播給訂閱者）\n"
        "我的ID（取得你的 LINE ID）\n\n"
        f"{DISCLAIMER}"
    )


def other_info_text():
    return (
        "🌐 川投顧｜其他金融資訊\n\n"
        "🥇 黃金：輸入「黃金」\n"
        "💱 美元：輸入「美元」\n"
        "💱 日圓：輸入「日圓」\n"
        "₿ 比特幣：輸入「比特幣」\n\n"
        "📌 其他功能請輸入「選單」。"
    )


def custom_period_prompt(token):
    alias = alias_for(token)
    end = date.today()
    start = end - timedelta(days=30)
    return (
        "📅 自訂時間查詢\n"
        "請依下列格式輸入（日期格式 YYYY-MM-DD）：\n\n"
        f"自訂 {alias} 開始日期 結束日期\n\n"
        "範例：\n"
        f"自訂 {alias} {start.isoformat()} {end.isoformat()}"
    )


# ==================================================
# 7. 即時股價（含 ETF、黃金、外匯、比特幣）：今日漲跌
# ==================================================

def query_today(token):
    try:
        target = resolve_target(token)
        hist = fetch_asset_history(target["symbol"], period="5d")

        if hist.empty:
            if target["is_asset"]:
                return f"{target['display']} 報價暫時無法取得。"
            return f"查不到 {target['symbol']} 的股價資料，請確認代碼是否正確。"

        return quote_card(target, hist)
    except Exception as exc:
        logger.exception("今日行情查詢錯誤")
        return f"查詢失敗：{type(exc).__name__}"


# ==================================================
# 8. 歷史股價與期間漲跌
# ==================================================

PERIOD_MAP = {
    "當天": "1d",
    "一天": "1d",
    "一週": "5d",
    "一個月": "1mo",
    "三個月": "3mo",
    "半年": "6mo",
    "一年": "1y",
}


def query_period(token, period_name):
    try:
        target = resolve_target(token)
        today = date.today()

        if period_name.upper() in ("年初至今", "今年", "YTD"):
            label = "年初至今"
            hist = fetch_asset_history(
                target["symbol"],
                start=date(today.year, 1, 1).isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
            )
        else:
            label = period_name
            hist = fetch_asset_history(
                target["symbol"], period=PERIOD_MAP.get(period_name, "1mo")
            )

        if hist.empty:
            return f"查不到 {target['code']} 的歷史股價。"

        return period_card(target, hist, f"查詢區間：{label}")
    except Exception as exc:
        logger.exception("期間查詢錯誤")
        return f"歷史股價查詢失敗：{type(exc).__name__}"


def query_custom_period(token, start_date, end_date):
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return "日期格式錯誤，請使用 YYYY-MM-DD。"

    if start >= end:
        return "日期格式錯誤：開始日期必須早於結束日期。"

    try:
        target = resolve_target(token)
        hist = fetch_asset_history(
            target["symbol"],
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
        )

        if hist.empty:
            return "這個日期區間沒有查到股價資料。"

        return period_card(target, hist, f"自訂區間：{start} 至 {end}")
    except Exception as exc:
        logger.exception("自訂區間查詢錯誤")
        return f"歷史股價查詢失敗：{type(exc).__name__}"


# ==================================================
# 9. 本益比與 EPS：最新股價 ÷ 最近四季 EPS
# ==================================================

def query_valuation(stock_id):
    stock_id = stock_id.strip().upper()

    try:
        # 台股（含 ETF）自動判斷 .TW / .TWO；美股直接使用股票代號
        ticker_symbol = normalize_ticker(stock_id)

        # 1. 取得最近可用的股價
        hist = fetch_history(ticker_symbol, period="5d")

        if hist.empty:
            return (
                f"⚠️【{stock_id} 本益比查詢】\n"
                "目前無法取得股價，請稍後再試。"
            )

        latest_price = float(hist.iloc[-1]["Close"])
        price_date = hist.index[-1].strftime("%Y-%m-%d")

        # 2. 取得公司名稱與幣別
        company_name, currency = stock_meta(stock_id, ticker_symbol)
        currency_text = f" {currency}" if currency else ""
        title = f"⚖️ {company_name}（{stock_id}）" if company_name != stock_id else f"⚖️ {stock_id}"

        # 3. 取得最近四季財報
        quarterly = get_quarterly(ticker_symbol)

        eps_values = []

        if quarterly is not None and not quarterly.empty:
            # Yahoo Finance 不同股票的 EPS 欄位名稱可能不同
            possible_rows = ["Diluted EPS", "Basic EPS"]

            eps_row_name = None
            for row_name in possible_rows:
                if row_name in quarterly.index:
                    eps_row_name = row_name
                    break

            if eps_row_name is not None:
                # 由最新季度排到最舊季度
                columns = sorted(
                    quarterly.columns,
                    key=lambda col: str(col),
                    reverse=True,
                )

                for col in columns:
                    value = to_number(quarterly.loc[eps_row_name, col])
                    if value is None:
                        continue

                    eps_values.append(value)

                    if len(eps_values) == 4:
                        break

        # 4. 必須取得四季 EPS，才自行計算 PE
        if len(eps_values) < 4:
            rows = [
                R("最新可用股價", f"{latest_price:,.2f}{currency_text}"),
                R("最近四季 EPS", "資料不足"),
                SEP,
                R("本益比（PE）", "無法計算", big=True, color=COLOR_FLAT),
            ]
            footer = [
                f"股價資料日期：{price_date}",
                "原因：Yahoo Finance 未提供足夠的季度 EPS（ETF 通常沒有 EPS）。",
            ]
            return make_card(title, "估值（本益比與 EPS）", COLOR_INFO, rows, footer)

        # 5. 加總最近四季 EPS
        eps_ttm = sum(eps_values)

        # EPS 必須大於 0，才顯示一般正本益比
        if eps_ttm > 0:
            pe = latest_price / eps_ttm
            pe_text = f"{pe:.2f} 倍"
        elif eps_ttm < 0:
            pe_text = "不適用（EPS 為負）"
        else:
            pe_text = "不適用（EPS 為零）"

        rows = [
            R("最新可用股價", f"{latest_price:,.2f}{currency_text}"),
            R("最近四季 EPS 合計", f"{eps_ttm:.2f}"),
        ]
        for i, value in enumerate(eps_values):
            rows.append(R(f"　第{i + 1}季 EPS", f"{value:.2f}"))
        rows += [
            SEP,
            R("本益比（PE）", pe_text, big=True, color=COLOR_INFO),
        ]

        footer = [
            f"股價資料日期：{price_date}",
            "計算方式：最新可用股價 ÷ 最近四季 EPS 合計",
            "EPS 與股價的資料日期可能不同，請以各自公布日期為準。",
        ]
        return make_card(title, "估值（本益比與 EPS）", COLOR_INFO, rows, footer)

    except Exception as exc:
        logger.exception("估值查詢錯誤")
        return (
            f"⚠️ 查詢 {stock_id} 的估值時發生錯誤："
            f"{type(exc).__name__}"
        )


# ==================================================
# 10. 三大法人與籌碼
# ==================================================

def format_net_buy(net):
    if net is None:
        return "目前無資料"
    net_lots = net / 1000
    if net_lots > 0:
        return f"買超 +{net_lots:,.0f} 張"
    elif net_lots < 0:
        return f"賣超 {net_lots:,.0f} 張"
    else:
        return "持平 0 張"


def net_color(net):
    if net is None:
        return None
    return trend_color(net)


def query_institutional(symbol):
    try:
        stock_id = symbol.strip().upper()
        if not is_tw_code(stock_id):
            return "籌碼查詢目前僅支援台股代碼，例如：籌碼 2330。"

        start_date = (date.today() - timedelta(days=30)).isoformat()
        institutional_data = get_finmind_data(
            "TaiwanStockInstitutionalInvestorsBuySell",
            stock_id=stock_id,
            start_date=start_date,
        )

        institutional_date = None
        foreign_net, trust_net, dealer_net = None, None, None

        latest_inst = get_latest_row(institutional_data)
        if latest_inst:
            latest_date = str(latest_inst["date"])
            latest_rows = [
                row for row in institutional_data
                if isinstance(row, dict) and str(row.get("date", "")) == latest_date
            ]
            institutional_date = latest_date

            f_b, f_s, t_b, t_s, d_b, d_s = 0, 0, 0, 0, 0, 0
            has_f, has_t, has_d = False, False, False

            for row in latest_rows:
                name = str(row.get("name", ""))
                buy = to_number(row.get("buy"))
                sell = to_number(row.get("sell"))
                if buy is None or sell is None:
                    continue
                if "Foreign" in name and "Self" not in name:
                    f_b += buy
                    f_s += sell
                    has_f = True
                elif "Investment_Trust" in name:
                    t_b += buy
                    t_s += sell
                    has_t = True
                elif "Dealer" in name:
                    d_b += buy
                    d_s += sell
                    has_d = True

            if has_f:
                foreign_net = f_b - f_s
            if has_t:
                trust_net = t_b - t_s
            if has_d:
                dealer_net = d_b - d_s

        nets = [v for v in (foreign_net, trust_net, dealer_net) if v is not None]
        total_net = sum(nets) if nets else None

        shareholding_data = get_finmind_data(
            "TaiwanStockShareholding", stock_id=stock_id, start_date=start_date
        )
        shareholding = get_latest_row(shareholding_data)
        foreign_ratio = (
            to_number(shareholding.get("ForeignInvestmentSharesRatio"))
            if shareholding else None
        )
        ratio_text = (
            f"{foreign_ratio:.2f}%" if foreign_ratio is not None else "目前無資料"
        )

        rows = [
            R("外資買賣超", format_net_buy(foreign_net), color=net_color(foreign_net)),
            R("投信買賣超", format_net_buy(trust_net), color=net_color(trust_net)),
            R("自營商買賣", format_net_buy(dealer_net), color=net_color(dealer_net)),
            SEP,
            R("三大法人合計", format_net_buy(total_net), big=True, color=net_color(total_net)),
            SEP,
            R("外資持股比例", ratio_text),
        ]
        footer = [f"資料日期：{institutional_date or '目前無資料'}"]

        return make_card(
            f"🏦 {stock_display(stock_id)}",
            "三大法人與籌碼",
            COLOR_INFO,
            rows,
            footer,
        )
    except Exception:
        logger.exception("籌碼查詢錯誤")
        return "查詢籌碼時發生異常。"


def query_foreign_shareholding(symbol):
    stock_id = symbol.strip().upper()
    if not is_tw_code(stock_id):
        return "外資持股查詢目前僅支援台股代碼，例如：外資 2330。"

    data = get_finmind_data(
        "TaiwanStockShareholding",
        stock_id=stock_id,
        start_date=(date.today() - timedelta(days=30)).isoformat(),
    )
    row = get_latest_row(data)
    if not row:
        return f"🌐 {stock_id} 外資持股目前無資料。"

    ratio = to_number(row.get("ForeignInvestmentSharesRatio"))
    ratio_text = f"{ratio:.2f}%" if ratio is not None else "目前無資料"

    rows = [R("外資持股比例", ratio_text, big=True, color=COLOR_INFO)]
    footer = [f"資料日期：{row.get('date', '目前無資料')}"]
    return make_card(
        f"🌐 {stock_display(stock_id)}", "外資持股", COLOR_INFO, rows, footer
    )


# ==================================================
# 10.5 財經新聞推播（新聞內容在 news.py）
# ==================================================

def push_news():
    """把今日新聞推播給 NEWS_USER_IDS 裡的所有人，回傳結果說明文字。"""
    recipients = news_recipients()
    if not recipients:
        return "沒有設定 NEWS_USER_IDS，未推播。"

    items = fetch_news()
    if not items:
        return "沒有抓到新聞，未推播。"

    message = build_message(news_card(items))

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        for i in range(0, len(recipients), 500):
            api.multicast(MulticastRequest(to=recipients[i:i + 500], messages=[message]))

    return f"已推播 {len(items)} 則新聞給 {len(recipients)} 位。"


# ==================================================
# 11. 指令解析路由
#     回傳 (內容, 快速選單)；內容可以是文字或卡片
# ==================================================

PERIOD_WORDS = "當天|一天|一週|一個月|三個月|半年|一年|年初至今|今年|YTD"
SYMBOL_RE = re.compile(r"[A-Za-z0-9.^=_-]{1,20}")


def valid_target(token):
    return token.lower() in ASSET_ALIASES or bool(SYMBOL_RE.fullmatch(token))


def handle_command(text, user_id=None):
    default_quick = quick_reply_items()

    text = text.strip()
    if not text:
        return "請輸入指令，輸入「選單」查看完整功能。", default_quick

    command = text.lower()

    if command in ("新聞", "財經新聞", "news"):
        return query_news(), default_quick

    if command in ("我的id", "myid", "id"):
        if not user_id:
            return "目前無法取得你的 LINE ID，請在一對一聊天室輸入「我的ID」。", default_quick
        return (
            "🆔 你的 LINE ID：\n"
            f"{user_id}\n\n"
            "想訂閱每天早上的財經新聞，請把這串 ID 傳給管理員加入名單。"
        ), default_quick

    if command in ("選單", "菜單", "menu", "help", "幫助", "說明", "功能", "功能選單", "完整選單", "使用說明"):
        return help_text(), default_quick

    if command in ("其他資訊", "其他", "other_info"):
        return other_info_text(), default_quick

    # 黃金、外匯、比特幣：先顯示今日漲跌，下方給時間單位按鈕
    if command in ASSET_ALIASES:
        return query_today(text), period_quick(alias_for(text))

    # 自訂日期（有給日期）
    custom_match = re.fullmatch(
        r"自訂\s+(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})", text
    )
    if custom_match and valid_target(custom_match.group(1)):
        token = custom_match.group(1)
        return (
            query_custom_period(token, custom_match.group(2), custom_match.group(3)),
            period_quick(alias_for(token), include_today=True),
        )

    # 自訂日期（只有代碼：提示輸入格式）
    custom_prompt_match = re.fullmatch(r"自訂\s+(\S+)", text)
    if custom_prompt_match and valid_target(custom_prompt_match.group(1)):
        token = custom_prompt_match.group(1)
        return custom_period_prompt(token), period_quick(alias_for(token), include_today=True)

    # 籌碼
    chip_match = re.fullmatch(r"(?:籌碼|法人|三大法人)\s+([A-Za-z0-9.^=_-]+)", text, flags=re.IGNORECASE)
    if chip_match:
        return query_institutional(chip_match.group(1)), default_quick

    # 外資持股
    foreign_match = re.fullmatch(r"外資\s+(\d{4,6}[A-Za-z]?)", text)
    if foreign_match:
        return query_foreign_shareholding(foreign_match.group(1)), default_quick

    # 本益比
    pe_match = re.fullmatch(r"(?:本益比|估值|EPS)\s+([A-Za-z0-9.^=_-]+)", text, flags=re.IGNORECASE)
    if pe_match:
        return query_valuation(pe_match.group(1)), default_quick

    if text in ("本益比", "估值", "EPS", "eps"):
        return query_valuation("2330"), default_quick

    # 歷史
    history_match = re.fullmatch(r"歷史\s+(\S+)", text)
    if history_match and valid_target(history_match.group(1)):
        token = history_match.group(1)
        return query_period(token, "一個月"), period_quick(alias_for(token), include_today=True)

    # 期間查詢（股票、ETF、黃金、外匯、比特幣都適用）
    period_match = re.fullmatch(rf"(\S+)\s+({PERIOD_WORDS})", text, flags=re.IGNORECASE)
    if period_match and valid_target(period_match.group(1)):
        token = period_match.group(1)
        return (
            query_period(token, period_match.group(2)),
            period_quick(alias_for(token), include_today=True),
        )

    # 即時股價（股票或 ETF，大小寫皆可，不用加 .TW）
    if SYMBOL_RE.fullmatch(text):
        if command in ("hi", "hello", "start", "開始"):
            return help_text(), default_quick
        return query_today(text), period_quick(alias_for(text))

    return "看不懂這個指令，請輸入「選單」查看完整功能。", default_quick


# ==================================================
# 12. LINE Webhook
# ==================================================

@app.route("/", methods=["GET"])
def home():
    return "川投顧 LINE Bot is running."


@app.route("/push_news", methods=["GET", "POST"])
def push_news_route():
    """給排程（例如 cron-job.org）每天早上呼叫：/push_news?key=你的CRON_SECRET"""
    key = request.args.get("key", "")
    if not CRON_SECRET or not hmac.compare_digest(key, CRON_SECRET):
        abort(403)
    try:
        return push_news()
    except Exception:
        logger.exception("新聞推播失敗")
        abort(500)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning("LINE 簽章驗證失敗")
        abort(400)
    except Exception:
        logger.exception("Webhook 處理失敗")
        abort(500)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text or ""
    try:
        user_id = getattr(event.source, "user_id", None)
        content, quick = handle_command(user_text, user_id=user_id)
        send_reply(event.reply_token, content, quick_replies=quick)
    except Exception:
        logger.exception("處理訊息失敗")
        try:
            send_reply(event.reply_token, "系統暫時忙碌，請稍後再試。", quick_replies=quick_reply_items())
        except Exception:
            logger.exception("備用回覆也失敗")


# ==================================================
# 13. 啟動（正式環境請用：gunicorn app:app）
# ==================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
