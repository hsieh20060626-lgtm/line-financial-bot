
import os
import re
import logging
from datetime import datetime, timedelta, date

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


# =========================================================
# 基本設定
# =========================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")

configuration = Configuration(
    access_token=CHANNEL_ACCESS_TOKEN
)
handler = WebhookHandler(CHANNEL_SECRET)

HTTP_TIMEOUT = 15


# =========================================================
# 共用工具
# =========================================================

def normalize_ticker(symbol):
    """將台股代碼轉成 Yahoo Finance 可使用的代碼。"""
    symbol = symbol.strip().upper()

    if re.fullmatch(r"\d{4,6}", symbol):
        return f"{symbol}.TW"

    return symbol


def format_number(value, digits=2):
    try:
        if value is None:
            return "目前無資料"

        value = float(value)

        if value != value:
            return "目前無資料"

        return f"{value:,.{digits}f}"

    except (TypeError, ValueError):
        return "目前無資料"


def get_stock_history(symbol, period="1mo"):
    ticker = normalize_ticker(symbol)
    stock = yf.Ticker(ticker)
    history = stock.history(period=period, auto_adjust=False)

    if history is None or history.empty:
        return None, None

    return stock, history


def format_change(change):
    if change > 0:
        return f"🔴 上漲 +{change:.2f}%"
    elif change < 0:
        return f"🟢 下跌 {change:.2f}%"
    return "⚪ 持平 0.00%"


def format_price_change(change):
    if change > 0:
        return f"🔴 +{change:,.2f}"
    elif change < 0:
        return f"🟢 {change:,.2f}"
    return "⚪ 0.00"


def get_quick_reply():
    labels = [
        "當天",
        "一週",
        "一個月",
        "三個月",
        "年初至今",
        "自訂期間",
    ]

    return QuickReply(
        items=[
            QuickReplyItem(
                action=MessageAction(
                    label=label,
                    text=label
                )
            )
            for label in labels
        ]
    )


# =========================================================
# 即時股價
# =========================================================

def query_stock(symbol):
    symbol = symbol.upper()
    ticker = normalize_ticker(symbol)

    try:
        stock = yf.Ticker(ticker)
        history = stock.history(period="5d", auto_adjust=False)

        if history is None or history.empty:
            return (
                f"查詢失敗：找不到 {symbol} 的行情。\n"
                "請確認股票代碼，或稍後再試。"
            ), None

        latest = history.iloc[-1]
        current_price = float(latest["Close"])

        if len(history) >= 2:
            previous_close = float(history.iloc[-2]["Close"])
        else:
            previous_close = current_price

        change = current_price - previous_close

        if previous_close:
            change_percent = change / previous_close * 100
        else:
            change_percent = 0

        currency = "美元" if not ticker.endswith(".TW") else "新臺幣"

        message = (
            f"📈【{symbol} 即時行情】\n"
            f"📅 資料日期：{history.index[-1].strftime('%Y-%m-%d')}\n"
            "------------------------------------\n"
            f"💰 最新價格：{current_price:,.2f} {currency}\n"
            f"📊 漲跌金額：{format_price_change(change)}\n"
            f"📉 漲跌幅：{format_change(change_percent)}\n"
            "------------------------------------\n"
            "📌 資料來源：Yahoo Finance\n"
            "⚠️ 報價可能延遲，僅供參考。"
        )

        return message, get_quick_reply()

    except Exception:
        logger.exception("query_stock failed")
        return "查詢股價時發生錯誤，請稍後再試。", None


# =========================================================
# 歷史期間漲跌幅
# =========================================================

def query_period(symbol, period_name):
    symbol = symbol.upper()
    ticker = normalize_ticker(symbol)

    period_map = {
        "當天": ("5d", "當天"),
        "一週": ("5d", "一週"),
        "一個月": ("1mo", "一個月"),
        "三個月": ("3mo", "三個月"),
        "年初至今": ("ytd", "年初至今"),
    }

    if period_name not in period_map:
        return "不支援的查詢期間。", None

    period, display_name = period_map[period_name]

    try:
        stock = yf.Ticker(ticker)
        history = stock.history(period=period, auto_adjust=False)

        if history is None or history.empty:
            return f"查無 {symbol} 的歷史行情。", None

        if period_name == "當天":
            if len(history) >= 2:
                start_price = float(history.iloc[-2]["Close"])
                end_price = float(history.iloc[-1]["Close"])
                start_date = history.index[-2]
            else:
                start_price = float(history.iloc[-1]["Open"])
                end_price = float(history.iloc[-1]["Close"])
                start_date = history.index[-1]
        elif period_name == "一週":
            start_price = float(history.iloc[0]["Close"])
            end_price = float(history.iloc[-1]["Close"])
            start_date = history.index[0]
        else:
            start_price = float(history.iloc[0]["Close"])
            end_price = float(history.iloc[-1]["Close"])
            start_date = history.index[0]

        if start_price == 0:
            return "無法計算漲跌幅：起始價格為 0。", None

        change = end_price - start_price
        change_percent = change / start_price * 100

        message = (
            f"📊【{symbol} 歷史漲跌幅】\n"
            f"🗓️ 查詢期間：{display_name}\n"
            f"📅 起始日期：{start_date.strftime('%Y-%m-%d')}\n"
            f"📅 結束日期：{history.index[-1].strftime('%Y-%m-%d')}\n"
            "------------------------------------\n"
            f"💵 起始收盤價：{start_price:,.2f}\n"
            f"💰 最新收盤價：{end_price:,.2f}\n"
            f"📈 漲跌金額：{format_price_change(change)}\n"
            f"📉 漲跌幅：{format_change(change_percent)}\n"
            "------------------------------------\n"
            "📌 資料來源：Yahoo Finance"
        )

        return message, None

    except Exception:
        logger.exception("query_period failed")
        return "查詢歷史漲跌幅時發生錯誤。", None


# =========================================================
# 自訂日期區間
# 使用格式：2330 2020-01-01 2026-01-01
# =========================================================

def query_custom_period(symbol, start_date, end_date):
    symbol = symbol.upper()
    ticker = normalize_ticker(symbol)

    try:
        start_day = datetime.strptime(
            start_date, "%Y-%m-%d"
        ).date()

        end_day = datetime.strptime(
            end_date, "%Y-%m-%d"
        ).date()

        if start_day >= end_day:
            return "日期錯誤：結束日期必須晚於開始日期。", None

        if end_day > date.today():
            return "日期錯誤：結束日期不能晚於今天。", None

        stock = yf.Ticker(ticker)

        history = stock.history(
            start=start_day.isoformat(),
            end=(end_day + timedelta(days=1)).isoformat(),
            auto_adjust=False
        )

        if history is None or history.empty:
            return (
                f"查無 {symbol} 在 {start_date} 至 "
                f"{end_date} 之間的歷史行情。"
            ), None

        start_price = float(history.iloc[0]["Close"])
        end_price = float(history.iloc[-1]["Close"])

        if start_price == 0:
            return "無法計算漲跌幅：起始價格為 0。", None

        change = end_price - start_price
        change_percent = change / start_price * 100

        message = (
            f"📊【{symbol} 自訂期間分析】\n"
            f"📅 查詢起日：{start_date}\n"
            f"📅 查詢迄日：{end_date}\n"
            "------------------------------------\n"
            f"💵 起始收盤價：{start_price:,.2f}\n"
            f"💰 結束收盤價：{end_price:,.2f}\n"
            f"📈 漲跌金額：{format_price_change(change)}\n"
            f"📉 漲跌幅：{format_change(change_percent)}\n"
            "------------------------------------\n"
            f"🔎 實際資料起日：{history.index[0].strftime('%Y-%m-%d')}\n"
            f"🔎 實際資料迄日：{history.index[-1].strftime('%Y-%m-%d')}\n"
            "📌 資料來源：Yahoo Finance"
        )

        return message, None

    except ValueError:
        return "日期格式錯誤，請使用 YYYY-MM-DD。", None

    except Exception:
        logger.exception("query_custom_period failed")
        return "查詢自訂日期區間時發生錯誤。", None


# =========================================================
# 本益比與 EPS
# =========================================================

def query_valuation(symbol, query_type="PE"):
    symbol = symbol.upper()
    ticker = normalize_ticker(symbol)

    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info:
            return f"查無 {symbol} 的財務資料。", None

        name = info.get("shortName", symbol)

        if query_type == "EPS":
            eps = info.get("trailingEps")
            forward_eps = info.get("forwardEps")

            message = (
                f"📘【{symbol} EPS 查詢】\n"
                f"🏢 公司名稱：{name}\n"
                "------------------------------------\n"
                f"📊 每股盈餘 EPS：{format_number(eps, 2)}\n"
                f"🔮 預估每股盈餘：{format_number(forward_eps, 2)}\n"
                "------------------------------------\n"
                "📌 資料來源：Yahoo Finance\n"
                "⚠️ 財務資料可能延遲，僅供參考。"
            )

        else:
            pe = info.get("trailingPE")
            forward_pe = info.get("forwardPE")

            message = (
                f"📘【{symbol} 本益比查詢】\n"
                f"🏢 公司名稱：{name}\n"
                "------------------------------------\n"
                f"📊 歷史本益比：{format_number(pe, 2)}\n"
                f"🔮 預估本益比：{format_number(forward_pe, 2)}\n"
                "------------------------------------\n"
                "📌 資料來源：Yahoo Finance\n"
                "⚠️ 本益比可能因公司虧損而無資料。"
            )

        return message, None

    except Exception:
        logger.exception("query_valuation failed")
        return "查詢財務指標時發生錯誤，請稍後再試。", None


# =========================================================
# 三大法人買賣超 + 融資融券
# =========================================================

def query_institutional(symbol):
    symbol = symbol.upper()

    if not re.fullmatch(r"\d{4,6}", symbol):
        return (
            "目前三大法人查詢僅支援數字型台股代碼，"
            "例如：籌碼 2330。"
        ), None

    today = datetime.now().date()
    match = None
    data_date = None

    try:
        # 往前尋找最近一筆有資料的交易日
        for offset in range(10):
            day = today - timedelta(days=offset)

            if day.weekday() >= 5:
                continue

            date_string = day.strftime("%Y%m%d")

            response = requests.get(
                "https://www.twse.com.tw/rwd/zh/fund/T86",
                params={
                    "date": date_string,
                    "selectType": "ALL",
                    "response": "json",
                },
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
            )

            if response.status_code != 200:
                continue

            data = response.json()
            fields = data.get("fields", [])
            rows = data.get("data", [])

            if not rows:
                continue

            code_index = None

            for index, field in enumerate(fields):
                if "證券代號" in field:
                    code_index = index
                    break

            if code_index is None:
                continue

            for row in rows:
                if len(row) > code_index and row[code_index].strip() == symbol:
                    match = row
                    data_date = day
                    break

            if match is not None:
                break

        if match is None:
            return (
                f"查無 {symbol} 最近的三大法人資料。\n"
                "請確認是否為上市股票，或稍後再試。"
            ), None

        def parse_int(value):
            try:
                cleaned = str(value).replace(",", "").strip()
                if cleaned in ("", "--", "-", "X", "N/A"):
                    return 0
                return int(float(cleaned))
            except (ValueError, TypeError):
                return 0

        def find_field(fields, row, keywords):
            for index, field in enumerate(fields):
                if all(keyword in field for keyword in keywords):
                    if index < len(row):
                        return parse_int(row[index])
            return 0

        # 重新取得當日欄位名稱
        response = requests.get(
            "https://www.twse.com.tw/rwd/zh/fund/T86",
            params={
                "date": data_date.strftime("%Y%m%d"),
                "selectType": "ALL",
                "response": "json",
            },
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = response.json()
        fields = data.get("fields", [])

        foreign_buy = find_field(
            fields, match, ["外陸資買賣超股數"]
        )
        trust_net = find_field(
            fields, match, ["投信買賣超股數"]
        )
        dealer_net = find_field(
            fields, match, ["自營商買賣超股數"]
        )

        # 外陸資買賣超股數在不同版本資料欄位可能不同。
        # 如果找不到合併欄位，嘗試將兩個外資欄位相加。
        if foreign_buy == 0:
            foreign_net_1 = find_field(
                fields, match, ["外資及陸資", "買賣超股數"]
            )
            foreign_net_2 = find_field(
                fields, match, ["外資自營商", "買賣超股數"]
            )
            foreign_buy = foreign_net_1 + foreign_net_2

        total_net = foreign_buy + trust_net + dealer_net

        def fmt_lots(shares):
            lots = round(shares / 1000)

            if lots > 0:
                return f"🔴 買超 +{lots:,} 張"
            elif lots < 0:
                return f"🟢 賣超 {lots:,} 張"
            return "⚪ 持平 0 張"

        institutional_text = (
            f"📊【{symbol} ({symbol})】法人買賣超與籌碼\n"
            f"📅 資料日期：{data_date:%Y-%m-%d}\n"
            "------------------------------------\n"
            f"🌍 外資買賣超：{fmt_lots(foreign_buy)}\n"
            f"🏦 投信買賣超：{fmt_lots(trust_net)}\n"
            f"💼 自營商買賣：{fmt_lots(dealer_net)}\n"
            f"📈 三大法人合計：{fmt_lots(total_net)}\n"
            "------------------------------------\n"
            "👑 外資持股比例：目前無資料\n"
            "👔 內部大股東持股：目前無資料\n"
            "👥 本土與一般投資人：目前無資料\n"
        )

    except Exception:
        logger.exception("TWSE institutional query failed")
        return "查詢三大法人資料時發生錯誤，請稍後再試。", None

    # FinMind 融資融券資料
    margin_text = ""

    if FINMIND_TOKEN:
        try:
            margin_response = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={
                    "dataset": "TaiwanStockMarginPurchaseShortSale",
                    "data_id": symbol,
                    "start_date": (
                        data_date - timedelta(days=14)
                    ).strftime("%Y-%m-%d"),
                    "end_date": data_date.strftime("%Y-%m-%d"),
                    "token": FINMIND_TOKEN,
                },
                timeout=HTTP_TIMEOUT,
            )

            margin_json = margin_response.json()
            margin_rows = margin_json.get("data", [])

            if margin_rows:
                latest = margin_rows[-1]

                margin_purchase = latest.get(
                    "MarginPurchaseTodayBalance"
                )
                short_sale = latest.get(
                    "ShortSaleTodayBalance"
                )

                margin_text = (
                    "\n📚【融資融券】\n"
                    "------------------------------------\n"
                    f"📈 融資餘額：{format_number(margin_purchase, 0)} 張/股資料值\n"
                    f"📉 融券餘額：{format_number(short_sale, 0)} 張/股資料值\n"
                    "📌 數值單位請以資料來源定義為準。\n"
                )

        except Exception:
            logger.exception("FinMind margin query failed")
            margin_text = "\n📚 融資融券：目前無法取得資料。\n"
    else:
        margin_text = "\n📚 融資融券：尚未設定 FinMind API Token。\n"

    return institutional_text + margin_text, None


# =========================================================
# 黃金價格
# =========================================================

def query_gold():
    try:
        ticker = yf.Ticker("GC=F")
        history = ticker.history(period="5d")

        if history is None or history.empty:
            return "目前無法取得黃金期貨價格。", None

        latest = history.iloc[-1]
        price = float(latest["Close"])

        return (
            "🥇【黃金期貨行情】\n"
            "------------------------------------\n"
            f"💰 最新價格：{price:,.2f} 美元/盎司\n"
            f"📅 資料日期：{history.index[-1].strftime('%Y-%m-%d')}\n"
            "------------------------------------\n"
            "📌 商品代碼：GC=F\n"
            "⚠️ 此為黃金期貨報價，不等於銀樓黃金牌價。"
        ), None

    except Exception:
        logger.exception("query_gold failed")
        return "查詢黃金價格時發生錯誤。", None


# =========================================================
# 外匯匯率
# =========================================================

def query_fx(pair):
    pair = pair.upper()

    supported = {
        "USD/TWD": "USDTWD=X",
        "USD/JPY": "USDJPY=X",
    }

    if pair not in supported:
        return (
            "目前支援的匯率：\n"
            "USD/TWD\n"
            "USD/JPY"
        ), None

    try:
        ticker = yf.Ticker(supported[pair])
        history = ticker.history(period="5d")

        if history is None or history.empty:
            return f"目前無法取得 {pair} 匯率。", None

        latest = history.iloc[-1]
        price = float(latest["Close"])

        return (
            f"💱【{pair} 匯率】\n"
            "------------------------------------\n"
            f"💰 最新匯率：{price:,.4f}\n"
            f"📅 資料日期：{history.index[-1].strftime('%Y-%m-%d')}\n"
            "------------------------------------\n"
            "📌 資料來源：Yahoo Finance\n"
            "⚠️ 報價可能延遲，僅供參考。"
        ), None

    except Exception:
        logger.exception("query_fx failed")
        return "查詢匯率時發生錯誤。", None


# =========================================================
# 比特幣
# =========================================================

def query_bitcoin():
    try:
        headers = {}

        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()
        btc = data.get("bitcoin", {})

        price = btc.get("usd")
        change = btc.get("usd_24h_change")

        if price is None:
            return "目前無法取得比特幣價格。", None

        change_text = (
            format_change(float(change))
            if change is not None
            else "目前無資料"
        )

        return (
            "₿【比特幣行情】\n"
            "------------------------------------\n"
            f"💰 最新價格：${float(price):,.2f} USD\n"
            f"📉 24 小時漲跌幅：{change_text}\n"
            "------------------------------------\n"
            "📌 資料來源：CoinGecko\n"
            "⚠️ 加密貨幣價格波動較大，僅供參考。"
        ), None

    except Exception:
        logger.exception("query_bitcoin failed")
        return "查詢比特幣價格時發生錯誤。", None


# =========================================================
# 功能說明
# =========================================================

def help_text():
    return (
        "🤖【川投顧｜功能選單】\n"
        "------------------------------------\n"
        "📈 即時股價\n"
        "輸入：2330、0050、AAPL\n\n"
        "📊 歷史漲跌幅\n"
        "先輸入股票代碼，再選擇期間按鈕。\n\n"
        "🗓️ 自訂日期區間\n"
        "格式：股票代碼 開始日期 結束日期\n"
        "例如：2330 2020-01-01 2026-01-01\n"
        "美股範例：AAPL 2020-01-01 2026-01-01\n\n"
        "📘 本益比\n"
        "輸入：本益比 2330\n\n"
        "📗 EPS\n"
        "輸入：EPS AAPL\n\n"
        "🏦 三大法人與融資融券\n"
        "輸入：籌碼 2330\n\n"
        "🥇 黃金\n"
        "輸入：黃金\n\n"
        "💱 匯率\n"
        "輸入：USD/TWD 或 USD/JPY\n\n"
        "₿ 比特幣\n"
        "輸入：比特幣\n"
        "------------------------------------\n"
        "⚠️ 本機器人提供資訊查詢，不構成投資建議。"
    )


# =========================================================
# 指令處理
# =========================================================

def handle_command(text):
    text = (text or "").strip()

    if not text:
        return "請輸入查詢指令，輸入「功能」查看使用方式。", None

    parts = text.split()
    command = parts[0].upper()

    # 功能選單
    if command in ["功能選單", "功能", "HELP", "說明"]:
        return help_text(), None

    # 三大法人與融資融券
    if command in ["籌碼", "法人"] and len(parts) >= 2:
        return query_institutional(parts[1])

    # 本益比
    if command in ["本益比", "PE"] and len(parts) >= 2:
        return query_valuation(parts[1], "PE")

    # EPS
    if command == "EPS" and len(parts) >= 2:
        return query_valuation(parts[1], "EPS")

    # 黃金
    if command in ["黃金", "黃金價格", "GOLD"]:
        return query_gold()

    # 比特幣
    if command in ["比特幣", "BTC", "BITCOIN"]:
        return query_bitcoin()

    # 外匯
    if command in ["USD/TWD", "USDTWD", "美金台幣"]:
        return query_fx("USD/TWD")

    if command in ["USD/JPY", "USDJPY", "美金日圓"]:
        return query_fx("USD/JPY")

    # 自訂日期區間
    # 例如：2330 2020-01-01 2026-01-01
    if (
        len(parts) == 3
        and re.fullmatch(r"[A-Za-z0-9.^=_-]+", parts[0])
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[2])
    ):
        return query_custom_period(
            parts[0],
            parts[1],
            parts[2]
        )

    # 歷史期間按鈕
    period_names = [
        "當天",
        "一週",
        "一個月",
        "三個月",
        "年初至今",
    ]

    if command in [name.upper() for name in period_names]:
        selected_period = next(
            name for name in period_names
            if name.upper() == command
        )

        # 使用者選擇期間後，必須知道要查哪一檔股票。
        # LINE 快速回覆送出的文字只有期間名稱，
        # 因此這裡提示使用者重新輸入代碼與期間。
        return (
            f"你選擇了「{selected_period}」。\n"
            "請重新輸入股票代碼，然後選擇期間按鈕，"
            "或直接使用自訂日期格式：\n"
            "2330 2020-01-01 2026-01-01"
        ), None

    # 直接輸入股票代碼
    if len(parts) == 1 and re.fullmatch(
        r"[A-Za-z0-9.^=_-]+", parts[0]
    ):
        return query_stock(parts[0])

    return (
        "我看不懂這個指令，請輸入「功能」查看使用方式。"
    ), None


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return "川投顧 LINE Bot is running.", 200


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning("Invalid LINE signature")
        abort(400)
    except Exception:
        logger.exception("Webhook handling failed")
        abort(500)

    return "OK", 200


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text or ""

    try:
        reply, quick_reply = handle_command(user_text)

        # 確保 LINE TextMessage 收到的是字串
        if not isinstance(reply, str):
            reply = str(reply)

        message = TextMessage(
            text=reply[:4500],
            quick_reply=quick_reply
        )

        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)

            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[message]
                )
            )

    except Exception:
        logger.exception("Failed to reply to LINE message")


# =========================================================
# 本機測試
# Render 使用 gunicorn main:app，不會執行此區塊
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )
