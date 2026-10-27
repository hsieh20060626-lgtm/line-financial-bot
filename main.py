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

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
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

        if not (float("-inf") < value < float("inf")):
            return "目前無資料"

        return f"{value:,.{digits}f}"

    except (TypeError, ValueError):
        return "目前無資料"


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


def get_quick_reply(symbol):
    """將股票代碼一起放進期間按鈕，避免忘記原本查詢的股票。"""
    labels = [
        "當天",
        "一週",
        "一個月",
        "三個月",
        "年初至今",
    ]

    return QuickReply(
        items=[
            QuickReplyItem(
                action=MessageAction(
                    label=label,
                    text=f"{symbol} {label}"
                )
            )
            for label in labels
        ]
    )


def parse_int(value):
    """將 API 回傳的數字字串轉成整數；無法解析時回傳 None。"""
    try:
        cleaned = str(value).replace(",", "").strip()

        if cleaned in ("", "--", "-", "X", "N/A", "None"):
            return None

        return int(float(cleaned))

    except (ValueError, TypeError):
        return None


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
            previous_close = float(latest["Open"])

        change = current_price - previous_close

        if previous_close:
            change_percent = change / previous_close * 100
        else:
            change_percent = 0

        currency = "新臺幣" if ticker.endswith(".TW") else "原報價幣別"

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

        return message, get_quick_reply(symbol)

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
        "當天": "5d",
        "一週": "5d",
        "一個月": "1mo",
        "三個月": "3mo",
        "年初至今": "ytd",
    }

    if period_name not in period_map:
        return "不支援的查詢期間。", None

    try:
        stock = yf.Ticker(ticker)

        if period_name == "年初至今":
            start_day = date(date.today().year, 1, 1)
            history = stock.history(
                start=start_day.isoformat(),
                end=(date.today() + timedelta(days=1)).isoformat(),
                auto_adjust=False,
            )
        else:
            history = stock.history(
                period=period_map[period_name],
                auto_adjust=False,
            )

        if history is None or history.empty:
            return f"查無 {symbol} 的歷史行情。", None

        if period_name == "當天":
            # 優先比較最近兩個交易日的收盤價。
            if len(history) >= 2:
                start_row = history.iloc[-2]
                end_row = history.iloc[-1]
                start_date = history.index[-2]
                start_price = float(start_row["Close"])
                end_price = float(end_row["Close"])
            else:
                start_date = history.index[-1]
                start_price = float(history.iloc[-1]["Open"])
                end_price = float(history.iloc[-1]["Close"])

        elif period_name == "一週":
            start_date = history.index[0]
            start_price = float(history.iloc[0]["Close"])
            end_price = float(history.iloc[-1]["Close"])

        else:
            start_date = history.index[0]
            start_price = float(history.iloc[0]["Close"])
            end_price = float(history.iloc[-1]["Close"])

        if start_price == 0:
            return "無法計算漲跌幅：起始價格為 0。", None

        change = end_price - start_price
        change_percent = change / start_price * 100

        message = (
            f"📊【{symbol} 歷史漲跌幅】\n"
            f"🗓️ 查詢期間：{period_name}\n"
            f"📅 起始日期：{start_date.strftime('%Y-%m-%d')}\n"
            f"📅 結束日期：{history.index[-1].strftime('%Y-%m-%d')}\n"
            "------------------------------------\n"
            f"💵 起始收盤價：{start_price:,.2f}\n"
            f"💰 最新收盤價：{end_price:,.2f}\n"
            f"📈 漲跌金額：{format_price_change(change)}\n"
            f"📉 漲跌幅：{format_change(change_percent)}\n"
            "------------------------------------\n"
            "📌 資料來源：Yahoo Finance\n"
            "⚠️ 期間計算以取得的交易日資料為準。"
        )

        return message, None

    except Exception:
        logger.exception("query_period failed")
        return "查詢歷史漲跌幅時發生錯誤。", None


# =========================================================
# 自訂日期區間
# 格式：2330 2020-01-01 2026-01-01
# =========================================================

def query_custom_period(symbol, start_date, end_date):
    symbol = symbol.upper()
    ticker = normalize_ticker(symbol)

    try:
        start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_day = datetime.strptime(end_date, "%Y-%m-%d").date()

        if start_day >= end_day:
            return "日期錯誤：結束日期必須晚於開始日期。", None

        if end_day > date.today():
            return "日期錯誤：結束日期不能晚於今天。", None

        stock = yf.Ticker(ticker)
        history = stock.history(
            start=start_day.isoformat(),
            end=(end_day + timedelta(days=1)).isoformat(),
            auto_adjust=False,
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
                f"📗【{symbol} EPS 查詢】\n"
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
# 外資持股比例
# 注意：僅在 API 實際回傳相符欄位時顯示
# =========================================================

def query_foreign_shareholding(symbol):
    if not FINMIND_TOKEN:
        return None

    try:
        today = date.today()

        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            headers={"Authorization": f"Bearer {FINMIND_TOKEN}"},
            params={
                "dataset": "TaiwanStockShareholding",
                "data_id": symbol,
                "start_date": (today - timedelta(days=14)).isoformat(),
                "end_date": today.isoformat(),
            },
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()

        rows = response.json().get("data", [])
        if not rows:
            return None

        row = max(rows, key=lambda item: item.get("date", ""))

        shares = row.get("ForeignInvestmentShares")
        ratio = row.get("ForeignInvestmentSharesRatio")

        if shares is None or ratio is None:
            logger.info("Foreign shareholding fields unavailable for %s", symbol)
            return None

        data_date = row.get("date", "未知")

        return (
            f"👑 外資持股股數：{int(float(shares)):,} 股\n"
            f"👑 外資持股比例：{float(ratio):.2f}%\n"
            f"📅 外資持股資料日期：{data_date}"
        )

    except Exception:
        logger.exception("Foreign shareholding query failed")
        return None


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

    today = date.today()
    match = None
    data_date = None
    fields = []

    try:
        # 往前尋找最近一筆有該股票資料的交易日。
        for offset in range(10):
            day = today - timedelta(days=offset)

            if day.weekday() >= 5:
                continue

            response = requests.get(
                "https://www.twse.com.tw/rwd/zh/fund/T86",
                params={
                    "date": day.strftime("%Y%m%d"),
                    "selectType": "ALL",
                    "response": "json",
                },
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()

            data = response.json()
            day_fields = data.get("fields", [])
            rows = data.get("data", [])

            if not rows or not day_fields:
                continue

            code_index = next(
                (
                    i for i, field in enumerate(day_fields)
                    if "證券代號" in field
                ),
                None,
            )

            if code_index is None:
                continue

            for row in rows:
                if (
                    len(row) > code_index
                    and str(row[code_index]).strip() == symbol
                ):
                    match = row
                    fields = day_fields
                    data_date = day
                    break

            if match is not None:
                break

        if match is None:
            return (
                f"查無 {symbol} 最近的三大法人資料。\n"
                "請確認股票是否為上市股票，或稍後再試。"
            ), None

        def find_field(keywords):
            for index, field in enumerate(fields):
                if all(word in field for word in keywords):
                    if index < len(match):
                        return parse_int(match[index])
            return None

        # 證交所不同版本的欄位名稱可能略有差異。
        foreign_buy = find_field(["外陸資買賣超股數"])
        if foreign_buy is None:
            foreign_buy = find_field(["外陸資", "買賣超股數"])

        trust_net = find_field(["投信買賣超股數"])
        if trust_net is None:
            trust_net = find_field(["投信", "買賣超股數"])

        dealer_net = find_field(["自營商買賣超股數"])
        if dealer_net is None:
            dealer_net = find_field(["自營商", "買賣超股數"])

        def fmt_lots(shares):
            if shares is None:
                return "目前無資料"

            lots = round(shares / 1000)

            if lots > 0:
                return f"🔴 買超 +{lots:,} 張"
            elif lots < 0:
                return f"🟢 賣超 {lots:,} 張"
            return "⚪ 持平 0 張"

        if all(value is not None for value in (
            foreign_buy, trust_net, dealer_net
        )):
            total_net = foreign_buy + trust_net + dealer_net
            total_text = fmt_lots(total_net)
        else:
            total_text = "目前無法完整計算"

        foreign_text = query_foreign_shareholding(symbol)

        institutional_text = (
            f"📊【{symbol} 三大法人與籌碼】\n"
            f"📅 資料日期：{data_date:%Y-%m-%d}\n"
            "------------------------------------\n"
            f"🌍 外資買賣超：{fmt_lots(foreign_buy)}\n"
            f"🏦 投信買賣超：{fmt_lots(trust_net)}\n"
            f"💼 自營商買賣：{fmt_lots(dealer_net)}\n"
            f"📈 三大法人合計：{total_text}\n"
            "------------------------------------\n"
            f"{foreign_text or '👑 外資持股比例：目前無資料'}\n"
            "👔 內部大股東持股：目前無資料\n"
            "👥 本土與一般投資人：目前無資料\n"
        )

    except Exception:
        logger.exception("TWSE institutional query failed")
        return "查詢三大法人資料時發生錯誤，請稍後再試。", None

    # 融資融券資料
    margin_text = ""

    if FINMIND_TOKEN:
        try:
            margin_response = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                headers={"Authorization": f"Bearer {FINMIND_TOKEN}"},
                params={
                    "dataset": "TaiwanStockMarginPurchaseShortSale",
                    "data_id": symbol,
                    "start_date": (
                        data_date - timedelta(days=14)
                    ).strftime("%Y-%m-%d"),
                    "end_date": data_date.strftime("%Y-%m-%d"),
                },
                timeout=HTTP_TIMEOUT,
            )
            margin_response.raise_for_status()

            margin_rows = margin_response.json().get("data", [])

            if margin_rows:
                latest = max(
                    margin_rows,
                    key=lambda item: item.get("date", "")
                )

                margin_purchase = latest.get("MarginPurchaseTodayBalance")
                short_sale = latest.get("ShortSaleTodayBalance")

                margin_text = (
                    "\n📚【融資融券】\n"
                    "------------------------------------\n"
                    f"📈 融資餘額：{format_number(margin_purchase, 0)}\n"
                    f"📉 融券餘額：{format_number(short_sale, 0)}\n"
                    f"📅 資料日期：{latest.get('date', '未知')}\n"
                    "📌 單位依 FinMind 資料集定義，請勿直接假設為張。\n"
                )
            else:
                margin_text = "\n📚【融資融券】目前查無資料。\n"

        except Exception:
            logger.exception("FinMind margin query failed")
            margin_text = "\n📚【融資融券】目前無法取得資料。\n"
    else:
        margin_text = "\n📚【融資融券】尚未設定 FinMind API Token。\n"

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
        "先輸入股票代碼，再按期間按鈕。\n"
        "也可以輸入：2330 一週\n\n"
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

    # 支援「2330 一週」這種期間查詢格式
    period_names = ["當天", "一週", "一個月", "三個月", "年初至今"]

    if len(parts) == 2 and parts[1] in period_names:
        return query_period(parts[0], parts[1])

    # 自訂日期區間
    if (
        len(parts) == 3
        and re.fullmatch(r"[A-Za-z0-9.^=_-]+", parts[0])
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[2])
    ):
        return query_custom_period(parts[0], parts[1], parts[2])

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

        if not isinstance(reply, str):
            reply = str(reply)

        message = TextMessage(
            text=reply[:4500],
            quick_reply=quick_reply,
        )

        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)

            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[message],
                )
            )

    except Exception:
        logger.exception("Failed to reply to LINE message")


# =========================================================
# 本機測試
# Render 使用 gunicorn main:app
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
    )
