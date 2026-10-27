import os
import re
import logging
from datetime import date, datetime, timedelta

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
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent


# ==================================================
# 1. 基本設定
# ==================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")

configuration = Configuration(
    access_token=LINE_CHANNEL_ACCESS_TOKEN
)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# ==================================================
# 2. 共用工具
# ==================================================

def get_line_api():
    """建立 LINE Messaging API 連線。"""
    return MessagingApi(ApiClient(configuration))


def send_text(reply_token, text, quick_replies=None):
    """回覆文字，並可附上快速選單。"""
    message = TextMessage(
        text=text[:4900],
        quick_reply=(
            QuickReply(items=quick_replies)
            if quick_replies else None
        ),
    )

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[message],
            )
        )


def quick_reply_items():
    """聊天室底部快速選單。"""
    labels = [
        ("完整選單", "選單"),
        ("即時股價", "2330"),
        ("法人籌碼", "籌碼 2330"),
        ("本益比/EPS", "本益比 2330"),
        ("歷史股價", "歷史 2330"),
        ("其他資訊", "其他資訊"),
    ]

    return [
        QuickReplyItem(
            action=MessageAction(
                label=label,
                text=command,
            )
        )
        for label, command in labels
    ]


def normalize_ticker(symbol):
    """將純數字台股代碼轉為 Yahoo Finance 常用格式。"""
    symbol = symbol.strip().upper()

    if symbol.isdigit():
        return symbol + ".TW"

    return symbol


def get_finmind_data(dataset, stock_id=None, start_date=None):
    """取得 FinMind 資料；沒有 Token 時回傳空資料。"""
    if not FINMIND_TOKEN:
        return []

    params = {
        "dataset": dataset,
        "token": FINMIND_TOKEN,
    }

    if stock_id:
        params["data_id"] = stock_id

    if start_date:
        params["start_date"] = start_date

    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("data", [])
    except Exception as exc:
        logger.warning("FinMind 查詢失敗：%s", exc)
        return []


# ==================================================
# 3. 完整功能選單／幫助說明
# ==================================================

def help_text():
    return (
        "📊 川投顧｜完整功能選單\n"
        "━━━━━━━━━━━━━━\n"
        "📈【一、即時股價】\n"
        "直接輸入股票代碼即可查詢。\n"
        "台股：2330、0050\n"
        "美股：AAPL、NVDA\n\n"
        "📉【二、歷史股價與漲跌】\n"
        "歷史 2330\n"
        "2330 一週\n"
        "2330 一個月\n"
        "2330 三個月\n"
        "2330 年初至今\n"
        "自訂 2330 2026-01-01 2026-10-01\n\n"
        "🏦【三、三大法人與籌碼】\n"
        "籌碼 2330\n"
        "查詢外資、投信、自營商買賣超等資料。\n\n"
        "🧮【四、本益比與 EPS】\n"
        "本益比 2330\n"
        "EPS 2330\n"
        "本益比 AAPL\n"
        "※ 資料是否完整取決於資料來源。\n\n"
        "🥇【五、黃金】\n"
        "黃金\n\n"
        "💱【六、外匯匯率】\n"
        "輸入美元或 USD：查美元兌新台幣\n"
        "輸入日圓或 JPY：查日圓兌新台幣\n"
        "輸入人民幣或 CNY：查人民幣兌新台幣\n"
        "輸入港幣或 HKD：查港幣兌新台幣\n"
        "輸入韓元或 KRW：查韓元兌新台幣\n"
        "輸入歐元或 EUR：查歐元兌新台幣\n\n"
        "₿【七、比特幣】\n"
        "比特幣\n"
        "BTC\n\n"
        "❓【其他指令】\n"
        "選單：顯示完整功能\n"
        "說明：顯示使用方式\n"
        "幫助：顯示使用方式\n"
        "其他資訊：顯示其他金融查詢\n"
        "━━━━━━━━━━━━━━\n"
        "⚠️ 本機器人提供金融資訊，不代表投資建議。"
    )


def other_info_text():
    return (
        "🌐 川投顧｜其他金融資訊\n\n"
        "🥇 黃金：輸入「黃金」\n"
        "💱 美元兌台幣：輸入「USD/TWD」\n"
        "💱 美元兌日圓：輸入「USD/JPY」\n"
        "💱 歐元兌美元：輸入「EUR/USD」\n"
        "₿ 比特幣：輸入「BTC」或「比特幣」\n\n"
        "📌 其他功能請輸入「選單」。"
    )


# ==================================================
# 4. 即時股價
# ==================================================

def query_stock(symbol):
    symbol = normalize_ticker(symbol)

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", auto_adjust=False)

        if hist.empty:
            return (
                f"查不到 {symbol} 的股價資料。\n"
                "請確認代碼是否正確。"
            )

        hist = hist.dropna(subset=["Close"])

        if hist.empty:
            return f"{symbol} 暫時沒有可用的收盤價資料。"

        latest = hist.iloc[-1]
        price = float(latest["Close"])

        if len(hist) >= 2:
            previous = float(hist.iloc[-2]["Close"])
        else:
            previous = price

        change = price - previous
        change_pct = (
            change / previous * 100
            if previous else 0
        )

        info = ticker.info or {}
        name = info.get("shortName") or info.get("longName") or symbol

        currency = info.get("currency", "")
        currency_text = f" {currency}" if currency else ""

        direction = "📈" if change >= 0 else "📉"

        return (
            f"{direction} {name}（{symbol}）\n"
            f"━━━━━━━━━━━━━━\n"
            f"最新可用價格：{price:,.2f}{currency_text}\n"
            f"前一交易日收盤：{previous:,.2f}{currency_text}\n"
            f"漲跌：{change:+,.2f}{currency_text}\n"
            f"漲跌幅：{change_pct:+.2f}%\n"
            f"資料日期：{hist.index[-1].strftime('%Y-%m-%d')}\n\n"
            "※ 報價可能延遲，不一定是即時成交價。"
        )

    except Exception as exc:
        logger.exception("股價查詢錯誤")
        return f"股價查詢失敗：{type(exc).__name__}"


# ==================================================
# 5. 歷史股價與期間漲跌
# ==================================================

def query_period(symbol, period_name):
    symbol = normalize_ticker(symbol)

    period_map = {
        "當天": "1d",
        "一天": "1d",
        "一週": "5d",
        "一個月": "1mo",
        "三個月": "3mo",
        "半年": "6mo",
        "一年": "1y",
    }

    try:
        ticker = yf.Ticker(symbol)
        today = date.today()

        if period_name in ("年初至今", "今年", "YTD"):
            hist = ticker.history(
                start=date(today.year, 1, 1).isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
                auto_adjust=False,
            )
        else:
            period = period_map.get(period_name)

            if not period:
                return (
                    "不支援這個期間。\n"
                    "可使用：當天、一週、一個月、三個月、半年、一年、年初至今。"
                )

            hist = ticker.history(
                period=period,
                auto_adjust=False,
            )

        hist = hist.dropna(subset=["Close"])

        if len(hist) < 1:
            return f"查不到 {symbol} 的歷史股價。"

        first_price = float(hist.iloc[0]["Close"])
        last_price = float(hist.iloc[-1]["Close"])

        change = last_price - first_price
        change_pct = (
            change / first_price * 100
            if first_price else 0
        )

        return (
            f"📊 {symbol}｜{period_name}股價表現\n"
            f"━━━━━━━━━━━━━━\n"
            f"起始日期：{hist.index[0].strftime('%Y-%m-%d')}\n"
            f"最新日期：{hist.index[-1].strftime('%Y-%m-%d')}\n"
            f"起始價格：{first_price:,.2f}\n"
            f"最新價格：{last_price:,.2f}\n"
            f"期間漲跌：{change:+,.2f}\n"
            f"期間漲跌幅：{change_pct:+.2f}%"
        )

    except Exception as exc:
        logger.exception("期間查詢錯誤")
        return f"歷史股價查詢失敗：{type(exc).__name__}"


def query_custom_period(symbol, start_date, end_date):
    symbol = normalize_ticker(symbol)

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        if start >= end:
            return "日期格式錯誤：開始日期必須早於結束日期。"

        if end > date.today() + timedelta(days=1):
            return "結束日期不能是未來日期。"

        hist = yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
        )

        hist = hist.dropna(subset=["Close"])

        if hist.empty:
            return "這個日期區間沒有查到股價資料。"

        first_price = float(hist.iloc[0]["Close"])
        last_price = float(hist.iloc[-1]["Close"])
        change = last_price - first_price
        change_pct = change / first_price * 100 if first_price else 0

        return (
            f"📊 {symbol}｜自訂日期區間\n"
            f"━━━━━━━━━━━━━━\n"
            f"查詢區間：{start} 至 {end}\n"
            f"起始價格：{first_price:,.2f}\n"
            f"結束價格：{last_price:,.2f}\n"
            f"期間漲跌：{change:+,.2f}\n"
            f"期間漲跌幅：{change_pct:+.2f}%"
        )

    except ValueError:
        return "日期格式錯誤，請使用 YYYY-MM-DD。"
    except Exception as exc:
        logger.exception("自訂日期查詢錯誤")
        return f"自訂日期查詢失敗：{type(exc).__name__}"


# ==================================================
# 6. 本益比與 EPS
# ==================================================

def query_valuation(symbol):
    symbol = normalize_ticker(symbol)

    try:
        info = yf.Ticker(symbol).info or {}

        name = info.get("shortName") or info.get("longName") or symbol
        pe = info.get("trailingPE")
        forward_pe = info.get("forwardPE")
        eps = info.get("trailingEps")
        forward_eps = info.get("forwardEps")
        currency = info.get("currency", "")

        def fmt(value):
            if value is None:
                return "無可用資料"
            try:
                return f"{float(value):,.2f}"
            except (ValueError, TypeError):
                return "無可用資料"

        return (
            f"🧮 {name}（{symbol}）估值資訊\n"
            f"━━━━━━━━━━━━━━\n"
            f"本益比（Trailing P/E）：{fmt(pe)}\n"
            f"預估本益比（Forward P/E）：{fmt(forward_pe)}\n"
            f"每股盈餘 EPS：{fmt(eps)} {currency}\n"
            f"預估 EPS：{fmt(forward_eps)} {currency}\n\n"
            "※ 資料可能缺漏或延遲；不同市場的會計與資料口徑可能不同。"
        )

    except Exception as exc:
        logger.exception("估值查詢錯誤")
        return f"估值查詢失敗：{type(exc).__name__}"


# ==================================================
# 7. 外資持股資訊（FinMind）
# ==================================================

def query_foreign_shareholding(symbol):
    stock_id = symbol.strip().upper()

    if not stock_id.isdigit():
        return "外資持股查詢目前僅支援台股代碼，例如：籌碼 2330。"

    data = get_finmind_data(
        "TaiwanStockShareholding",
        stock_id=stock_id,
        start_date=(date.today() - timedelta(days=30)).isoformat(),
    )

    if not data:
        return (
            "目前無法取得外資持股資料。\n"
            "請確認 FINMIND_TOKEN 是否設定，以及資料來源是否提供此資料集。"
        )

    row = data[-1]

    return (
        f"🌐 {stock_id} 外資持股資料\n"
        f"━━━━━━━━━━━━━━\n"
        f"日期：{row.get('date', '無資料')}\n"
        f"資料欄位：\n"
        + "\n".join(
            f"{key}：{value}"
            for key, value in row.items()
            if key not in ("stock_id", "date")
        )
        + "\n\n※ 欄位依資料來源回傳結果顯示。"
    )


# ==================================================
# 8. 三大法人買賣超
# ==================================================

def query_institutional(symbol):
    stock_id = symbol.strip().upper()

    if not stock_id.isdigit():
        return "法人籌碼目前僅支援台股代碼，例如：籌碼 2330。"

    # 證交所每日三大法人買賣超資料
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"

    try:
        response = requests.get(
            url,
            params={
                "date": date.today().strftime("%Y%m%d"),
                "selectType": "ALLBUT0999",
                "response": "json",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()

        fields = result.get("fields", [])
        rows = result.get("data", [])

        if not fields or not rows:
            # 交易所今日可能尚未更新，改查最近一個交易日
            return query_institutional_recent(stock_id)

        for row in rows:
            record = dict(zip(fields, row))
            code = str(record.get("證券代號", "")).strip()

            if code == stock_id:
                return (
                    f"🏦 {stock_id} 三大法人資料\n"
                    f"━━━━━━━━━━━━━━\n"
                    + "\n".join(
                        f"{key}：{value}"
                        for key, value in record.items()
                    )
                    + "\n\n※ 數值與欄位名稱依證交所資料顯示。"
                )

        return query_institutional_recent(stock_id)

    except Exception as exc:
        logger.exception("法人查詢錯誤")
        return (
            f"法人資料暫時無法取得：{type(exc).__name__}\n"
            "可能是休市日或交易所資料尚未更新。"
        )


def query_institutional_recent(stock_id):
    """查詢近幾個日期，避開週末及休市日。"""
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"

    for offset in range(1, 8):
        target = date.today() - timedelta(days=offset)

        try:
            response = requests.get(
                url,
                params={
                    "date": target.strftime("%Y%m%d"),
                    "selectType": "ALLBUT0999",
                    "response": "json",
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            response.raise_for_status()
            result = response.json()

            fields = result.get("fields", [])
            rows = result.get("data", [])

            if not fields or not rows:
                continue

            for row in rows:
                record = dict(zip(fields, row))

                if str(record.get("證券代號", "")).strip() == stock_id:
                    return (
                        f"🏦 {stock_id} 三大法人資料\n"
                        f"資料日期：{target}\n"
                        f"━━━━━━━━━━━━━━\n"
                        + "\n".join(
                            f"{key}：{value}"
                            for key, value in record.items()
                        )
                    )

        except Exception:
            continue

    return (
        f"最近幾個交易日都找不到 {stock_id} 的法人資料。\n"
        "請確認股票代碼，或稍後再試。"
    )


# ==================================================
# 9. 黃金、外匯、比特幣
# ==================================================

def query_gold():
    try:
        hist = yf.Ticker("GC=F").history(period="5d")
        hist = hist.dropna(subset=["Close"])

        if hist.empty:
            return "目前無法取得黃金期貨報價。"

        price = float(hist.iloc[-1]["Close"])
        day = hist.index[-1].strftime("%Y-%m-%d")

        return (
            "🥇 黃金期貨參考價格\n"
            f"最新可用價格：{price:,.2f} 美元／金衡盎司\n"
            f"資料日期：{day}\n\n"
            "※ 此為黃金期貨報價，不等同台灣銀樓實際金價。"
        )

    except Exception as exc:
        logger.exception("黃金查詢錯誤")
        return f"黃金查詢失敗：{type(exc).__name__}"


def query_fx(pair="USD/TWD"):
    currencies = {
        "USD": {"name": "美元", "ticker": "TWD=X", "direction": "USD/TWD"},
        "JPY": {"name": "日圓", "ticker": "JPY TWD=X", "direction": "JPY/TWD"},
        "CNY": {"name": "人民幣", "ticker": "CNYTWD=X", "direction": "CNY/TWD"},
        "HKD": {"name": "港幣", "ticker": "HKDTWD=X", "direction": "HKD/TWD"},
        "KRW": {"name": "韓元", "ticker": "KRWTWD=X", "direction": "KRW/TWD"},
        "EUR": {"name": "歐元", "ticker": "EURTWD=X", "direction": "EUR/TWD"},
    }

    aliases = {
        "美元": "USD", "美金": "USD", "USD": "USD",
        "日圓": "JPY", "日元": "JPY", "JPY": "JPY",
        "人民幣": "CNY", "人民币": "CNY", "RMB": "CNY", "CNY": "CNY",
        "港幣": "HKD", "港币": "HKD", "HKD": "HKD",
        "韓元": "KRW", "韩元": "KRW", "韓幣": "KRW", "KRW": "KRW",
        "歐元": "EUR", "欧元": "EUR", "EUR": "EUR",
    }

    pair = pair.strip().upper().replace(" ", "")

    if pair.endswith("/TWD"):
        pair = pair[:-4]

    currency_code = aliases.get(pair, pair)

    if currency_code not in currencies:
        return (
            "💱 川投顧｜匯率查詢\n\n"
            "請輸入：\n"
            "美元 / USD\n"
            "日圓 / JPY\n"
            "人民幣 / CNY\n"
            "港幣 / HKD\n"
            "韓元 / KRW\n"
            "歐元 / EUR"
        )

    item = currencies[currency_code]

    try:
        hist = yf.Ticker(item["ticker"]).history(period="5d")
        hist = hist.dropna(subset=["Close"])

        if hist.empty:
            return (
                f"目前無法取得 {item['name']} 匯率。\n"
                "請稍後再試，或確認資料來源是否支援該貨幣對。"
            )

        price = float(hist.iloc[-1]["Close"])
        day = hist.index[-1].strftime("%Y-%m-%d")

        return (
            f"💱 {item['name']}匯率參考\n"
            f"━━━━━━━━━━━━━━\n"
            f"匯率：{item['direction']}\n"
            f"1 單位外幣 ≈ {price:.6f} 新台幣\n"
            f"資料日期：{day}\n\n"
            "※ 市場參考價，非銀行實際買入或賣出價。"
        )

    except Exception as exc:
        logger.exception("匯率查詢錯誤")
        return f"匯率查詢失敗：{type(exc).__name__}"


def query_bitcoin():
    try:
        hist = yf.Ticker("BTC-USD").history(period="5d")
        hist = hist.dropna(subset=["Close"])

        if hist.empty:
            return "目前無法取得比特幣報價。"

        price = float(hist.iloc[-1]["Close"])
        day = hist.index[-1].strftime("%Y-%m-%d")

        if len(hist) >= 2:
            previous = float(hist.iloc[-2]["Close"])
        else:
            previous = price

        change = price - previous
        change_pct = change / previous * 100 if previous else 0

        return (
            "₿ 比特幣（BTC/USD）\n"
            f"最新可用價格：${price:,.2f}\n"
            f"漲跌：${change:+,.2f}\n"
            f"漲跌幅：{change_pct:+.2f}%\n"
            f"資料日期：{day}\n\n"
            "※ 加密貨幣價格波動劇烈，報價可能延遲。"
        )

    except Exception as exc:
        logger.exception("比特幣查詢錯誤")
        return f"比特幣查詢失敗：{type(exc).__name__}"


# ==================================================
# 10. 指令解析
# ==================================================

def handle_command(text):
    text = text.strip()

    if not text:
        return "請輸入指令，輸入「選單」查看完整功能。"

    command = text.lower()

    # 完整選單與幫助
    if command in (
        "選單", "菜單", "menu", "help", "幫助", "說明",
        "功能", "功能選單", "完整選單", "使用說明"
    ):
        return help_text()

    if command in ("其他資訊", "其他", "金融資訊"):
        return other_info_text()

    if command in ("黃金", "gold"):
        return query_gold()

    if command in ("比特幣", "btc", "bitcoin"):
        return query_bitcoin()

        # 匯率：中文幣別名稱、英文代碼或外幣兌新台幣
    fx_aliases = {
        "匯率", "外匯",
        "美元", "美金", "USD", "USD/TWD",
        "日圓", "日元", "JPY", "JPY/TWD",
        "人民幣", "人民币", "RMB", "CNY", "CNY/TWD",
        "港幣", "港币", "HKD", "HKD/TWD",
        "韓元", "韩元", "韓幣", "KRW", "KRW/TWD",
        "歐元", "欧元", "EUR", "EUR/TWD",
    }

    if command.upper() in fx_aliases:
        if command.lower() in ("匯率", "外匯"):
            return query_fx("USD")
        return query_fx(command)

    # 自訂日期：自訂 2330 2026-01-01 2026-10-01
    custom_match = re.fullmatch(
        r"自訂\s+([A-Za-z0-9.^=_-]+)\s+"
        r"(\d{4}-\d{2}-\d{2})\s+"
        r"(\d{4}-\d{2}-\d{2})",
        text,
    )

    if custom_match:
        return query_custom_period(
            custom_match.group(1),
            custom_match.group(2),
            custom_match.group(3),
        )

    # 法人籌碼：籌碼 2330
    chip_match = re.fullmatch(
        r"(?:籌碼|法人|三大法人)\s+([A-Za-z0-9.^=_-]+)",
        text,
        flags=re.IGNORECASE,
    )

    if chip_match:
        return query_institutional(chip_match.group(1))

    # 外資持股：外資 2330
    foreign_match = re.fullmatch(
        r"外資\s+(\d{4,6})",
        text,
    )

    if foreign_match:
        return query_foreign_shareholding(foreign_match.group(1))

    # 本益比：本益比 2330
    pe_match = re.fullmatch(
        r"(?:本益比|估值|EPS)\s+([A-Za-z0-9.^=_-]+)",
        text,
        flags=re.IGNORECASE,
    )

    if pe_match:
        return query_valuation(pe_match.group(1))

    # 歷史：歷史 2330
    history_match = re.fullmatch(
        r"歷史\s+([A-Za-z0-9.^=_-]+)",
        text,
        flags=re.IGNORECASE,
    )

    if history_match:
        return query_period(history_match.group(1), "一個月")

    # 期間查詢：2330 一週
    period_match = re.fullmatch(
        r"([A-Za-z0-9.^=_-]+)\s+"
        r"(當天|一天|一週|一個月|三個月|半年|一年|年初至今|今年|YTD)",
        text,
        flags=re.IGNORECASE,
    )

    if period_match:
        return query_period(
            period_match.group(1),
            period_match.group(2),
        )

    # 即時股價：2330、0050、AAPL
    if re.fullmatch(r"[A-Za-z0-9.^=_-]{1,20}", text):
        # 防止常見指令被當作股票代碼
        if command in (
            "hi", "hello", "start", "開始", "其他資訊"
        ):
            return help_text()

        return query_stock(text)

    return (
        "看不懂這個指令，請重新輸入。\n\n"
        "例如：\n"
        "2330\n"
        "籌碼 2330\n"
        "本益比 2330\n"
        "歷史 2330\n"
        "2330 一週\n"
        "自訂 2330 2026-01-01 2026-10-01\n\n"
        "輸入「選單」查看完整功能。"
    )


# ==================================================
# 11. LINE Webhook
# ==================================================

@app.route("/", methods=["GET"])
def home():
    return "川投顧 LINE Bot is running."


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning("LINE Webhook 簽章驗證失敗")
        abort(400)
    except Exception:
        logger.exception("Webhook 處理失敗")
        abort(500)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text or ""
    command = user_text.strip()

    try:
        answer = handle_command(command)

        # 選單、幫助、空白輸入時，附上快速按鈕
        if command.lower() in (
            "選單", "菜單", "menu", "help", "幫助",
            "說明", "功能", "功能選單", "完整選單",
            "使用說明", ""
        ):
            send_text(
                event.reply_token,
                answer,
                quick_replies=quick_reply_items(),
            )
        else:
            send_text(
                event.reply_token,
                answer,
                quick_replies=quick_reply_items(),
            )

    except Exception:
        logger.exception("處理 LINE 訊息失敗")

        try:
            send_text(
                event.reply_token,
                "目前系統忙碌或資料來源暫時無法使用，請稍後再試。\n"
                "輸入「選單」可以查看功能。",
                quick_replies=quick_reply_items(),
            )
        except Exception:
            logger.exception("LINE 錯誤訊息回覆失敗")


# ==================================================
# 12. 啟動
# ==================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
    )
