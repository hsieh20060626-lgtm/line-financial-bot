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


def query_valuation(stock_id):
    from datetime import datetime, timedelta

    stock_id = stock_id.strip().upper()
    today = datetime.now().date()
    start_date = (today - timedelta(days=120)).isoformat()

    # 1. 查詢本益比、股價淨值比、殖利率
    per_data = get_finmind_data(
        "TaiwanStockPER",
        stock_id=stock_id,
        start_date=start_date,
    )

    # 2. 查詢財報 EPS
    eps_data = get_finmind_data(
        "TaiwanStockFinancialStatements",
        stock_id=stock_id,
        start_date=(today - timedelta(days=730)).isoformat(),
    )

    # 本益比資料取日期最新的一筆
    per_data = sorted(
        per_data,
        key=lambda x: x.get("date", ""),
        reverse=True,
    )

    latest_per = per_data[0] if per_data else {}

    # 從財報中尋找 EPS 欄位
    eps_rows = [
        row for row in eps_data
        if "eps" in str(row.get("type", "")).lower()
        or "每股盈餘" in str(row.get("type", ""))
        or "每股盈餘" in str(row.get("origin_name", ""))
    ]

    eps_rows = sorted(
        eps_rows,
        key=lambda x: x.get("date", ""),
        reverse=True,
    )

    latest_eps = eps_rows[0] if eps_rows else {}

    # 組合回覆
    if not latest_per and not latest_eps:
        return (
            f"⚠️【{stock_id} 本益比與 EPS】\n"
            "目前查無資料。\n\n"
            "可能原因：\n"
            "1. FinMind API 沒有回傳資料\n"
            "2. 股票代號不正確\n"
            "3. API 權限或資料更新問題\n\n"
            "請稍後再試，或檢查 Render 日誌。"
        )

    lines = [
        f"📊【{stock_id} 本益比與 EPS】",
        "--------------------------------",
    ]

    if latest_per:
        lines.append(f"📅 本益比資料日期：{latest_per.get('date', '無資料')}")

        per = latest_per.get("PER")
        pbr = latest_per.get("PBR")
        dividend_yield = latest_per.get("dividend_yield")

        lines.append(
            f"📈 本益比：{per if per not in (None, '') else '無資料'}"
        )
        lines.append(
            f"📚 股價淨值比：{pbr if pbr not in (None, '') else '無資料'}"
        )
        lines.append(
            f"💰 殖利率：{dividend_yield if dividend_yield not in (None, '') else '無資料'}"
        )
    else:
        lines.append("📈 本益比：目前無資料")

    lines.append("--------------------------------")

    if latest_eps:
        lines.append(
            f"📅 EPS 財報日期：{latest_eps.get('date', '無資料')}"
        )
        lines.append(
            f"🧾 EPS 項目：{latest_eps.get('type', latest_eps.get('origin_name', '每股盈餘'))}"
        )
        lines.append(
            f"💵 EPS 數值：{latest_eps.get('value', '無資料')}（依原始資料單位）"
        )
    else:
        lines.append("💵 EPS：目前無法從回傳財報中辨識")

    lines.append("--------------------------------")
    lines.append("📌 本益比與 EPS 的資料日期可能不同，請以各自日期為準。")

    return "\n".join(lines)

# ==================================================
# 7、8. 完整籌碼報告：三大法人、外資持股、融資融券
# ==================================================

def format_net_buy(net):
    """將法人淨買賣超轉成張數顯示。"""
    if net is None:
        return "目前無資料"

    net_lots = net / 1000

    if net_lots > 0:
        return f"🟢 買超 +{net_lots:,.0f} 張"
    elif net_lots < 0:
        return f"🔴 賣超 {net_lots:,.0f} 張"
    else:
        return "⚪ 持平 0 張"


def to_number(value):
    """將資料轉為數值；缺漏時回傳 None。"""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_latest_row(data):
    """取日期最新的一筆資料。"""
    if not data:
        return None

    valid_rows = [
        row for row in data
        if row.get("date")
    ]

    if not valid_rows:
        return None

    return max(valid_rows, key=lambda row: str(row["date"]))


def query_institutional(symbol):
    stock_id = symbol.strip().upper()

    if not stock_id.isdigit():
        return "籌碼查詢目前僅支援台股代碼，例如：籌碼 2330。"

    start_date = (date.today() - timedelta(days=30)).isoformat()

    # --------------------------------------------------
    # A. 三大法人買賣超
    # --------------------------------------------------
    institutional_data = get_finmind_data(
        "TaiwanStockInstitutionalInvestorsBuySell",
        stock_id=stock_id,
        start_date=start_date,
    )

    institutional_date = None
    foreign_net = None
    trust_net = None
    dealer_net = None

    if institutional_data:
        latest_date = max(
            str(row.get("date", ""))
            for row in institutional_data
            if row.get("date")
        )

        latest_rows = [
            row for row in institutional_data
            if str(row.get("date", "")) == latest_date
        ]

        institutional_date = latest_date

        foreign_buy = 0
        foreign_sell = 0
        trust_buy = 0
        trust_sell = 0
        dealer_buy = 0
        dealer_sell = 0

        has_foreign = False
        has_trust = False
        has_dealer = False

        for row in latest_rows:
            name = str(row.get("name", ""))
            buy = to_number(row.get("buy"))
            sell = to_number(row.get("sell"))

            if buy is None or sell is None:
                continue

            if name == "Foreign_Investor":
                foreign_buy += buy
                foreign_sell += sell
                has_foreign = True

            elif name == "Foreign_Dealer_Self":
                # 外資自營商屬外資相關分類，不重複計入一般外資欄位
                pass

            elif name == "Investment_Trust":
                trust_buy += buy
                trust_sell += sell
                has_trust = True

            elif name in (
                "Dealer",
                "Dealer_self",
                "Dealer_Hedging",
            ):
                dealer_buy += buy
                dealer_sell += sell
                has_dealer = True

        if has_foreign:
            foreign_net = foreign_buy - foreign_sell

        if has_trust:
            trust_net = trust_buy - trust_sell

        if has_dealer:
            dealer_net = dealer_buy - dealer_sell

    total_net = None

    if any(value is not None for value in (
        foreign_net, trust_net, dealer_net
    )):
        total_net = sum(
            value for value in (
                foreign_net, trust_net, dealer_net
            )
            if value is not None
        )

    # --------------------------------------------------
    # B. 外資持股
    # --------------------------------------------------
    shareholding_data = get_finmind_data(
        "TaiwanStockShareholding",
        stock_id=stock_id,
        start_date=start_date,
    )

    shareholding = get_latest_row(shareholding_data)

    foreign_shares = None
    foreign_ratio = None
    shareholding_date = None

    if shareholding:
        foreign_shares = to_number(
            shareholding.get("ForeignInvestmentShares")
        )
        foreign_ratio = to_number(
            shareholding.get("ForeignInvestmentSharesRatio")
        )
        shareholding_date = shareholding.get("date")

    # --------------------------------------------------
    # C. 融資融券
    # --------------------------------------------------
    margin_data = get_finmind_data(
        "TaiwanStockMarginPurchaseShortSale",
        stock_id=stock_id,
        start_date=start_date,
    )

    margin = get_latest_row(margin_data)

    margin_balance = None
    short_balance = None
    margin_date = None

    if margin:
        margin_balance = to_number(
            margin.get("MarginPurchaseTodayBalance")
        )
        short_balance = to_number(
            margin.get("ShortSaleTodayBalance")
        )
        margin_date = margin.get("date")

    # --------------------------------------------------
    # D. 組成報告
    # --------------------------------------------------
    def show_number(value):
        if value is None:
            return "目前無資料"
        return f"{value:,.0f}"

    def show_ratio(value):
        if value is None:
            return "目前無資料"
        return f"{value:.2f}%"

    report = [
        f"📊【{stock_id} 三大法人與籌碼】",
        f"📅 資料日期：{institutional_date or '目前無資料'}",
        "------------------------------------",
        f"🌍 外資買賣超：{format_net_buy(foreign_net)}",
        f"🏦 投信買賣超：{format_net_buy(trust_net)}",
        f"💼 自營商買賣：{format_net_buy(dealer_net)}",
        f"📈 三大法人合計：{format_net_buy(total_net)}",
        "------------------------------------",
        f"👑 外資持股股數：{show_number(foreign_shares)} 股",
        f"👑 外資持股比例：{show_ratio(foreign_ratio)}",
        f"📅 外資持股資料日期：{shareholding_date or '目前無資料'}",
        "👔 內部大股東持股：目前無資料",
        "👥 本土與一般投資人：目前無資料",
        "",
        "📚【融資融券】",
        "------------------------------------",
        f"📈 融資餘額：{show_number(margin_balance)}",
        f"📉 融券餘額：{show_number(short_balance)}",
        f"📅 資料日期：{margin_date or '目前無資料'}",
        "📌 融資融券數值依 FinMind 資料集原始欄位顯示，請勿自行假設單位。",
    ]

    if not institutional_data and not shareholding and not margin:
        report.append("")
        report.append(
            "⚠️ 目前未取得籌碼資料，請確認 FINMIND_TOKEN、"
            "API 權限或資料來源狀態。"
        )

    return "\n".join(report)


def query_institutional_recent(stock_id):
    """
    相容舊程式呼叫。
    改由 FinMind 查詢最近資料，不再直接輸出證交所原始欄位。
    """
    return query_institutional(stock_id)


def query_foreign_shareholding(symbol):
    """
    保留原本的外資持股單獨查詢功能。
    """
    stock_id = symbol.strip().upper()

    if not stock_id.isdigit():
        return "外資持股查詢目前僅支援台股代碼，例如：外資 2330。"

    data = get_finmind_data(
        "TaiwanStockShareholding",
        stock_id=stock_id,
        start_date=(date.today() - timedelta(days=30)).isoformat(),
    )

    row = get_latest_row(data)

    if not row:
        return (
            f"🌐 {stock_id} 外資持股\n"
            "目前無資料，請確認 FinMind API 權限或稍後再試。"
        )

    shares = to_number(row.get("ForeignInvestmentShares"))
    ratio = to_number(row.get("ForeignInvestmentSharesRatio"))

    shares_text = (
        f"{shares:,.0f} 股" if shares is not None else "目前無資料"
    )
    ratio_text = (
        f"{ratio:.2f}%" if ratio is not None else "目前無資料"
    )

    return (
        f"🌐【{stock_id} 外資持股】\n"
        "------------------------------------\n"
        f"👑 外資持股股數：{shares_text}\n"
        f"👑 外資持股比例：{ratio_text}\n"
        f"📅 資料日期：{row.get('date', '目前無資料')}"
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
        "USD": {"name": "美元", "ticker": "USDTWD=X", "direction": "USD/TWD"},
        "JPY": {"name": "日圓", "ticker": "JPYTWD=X", "direction": "JPY/TWD"},
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
        ticker_symbol = item["ticker"]
        hist = yf.Ticker(ticker_symbol).history(period="5d")
        hist = hist.dropna(subset=["Close"])

        # 備案機制：如果直接抓不到，嘗試透過美元交叉換算或備用代碼
        if hist.empty and currency_code == "JPY":
            ticker_symbol = "JPY=X" # 國際日圓兌美元
            hist = yf.Ticker(ticker_symbol).history(period="5d")
            hist = hist.dropna(subset=["Close"])
            if not hist.empty:
                usdtwd_hist = yf.Ticker("USDTWD=X").history(period="1d")
                usdtwd = float(usdtwd_hist.iloc[-1]["Close"]) if not usdtwd_hist.empty else 32.5
                jpyusd = float(hist.iloc[-1]["Close"])
                # 換算為每 1 日圓對台幣（或者是直覺秀出匯率）
                price = usdtwd / (jpyusd * 100) if jpyusd > 0 else 0.21
            else:
                price = 0.2100 # 安全預設值
        elif hist.empty:
            return (
                f"目前無法取得 {item['name']} 匯率。\n"
                "請稍後再試，或確認資料來源是否支援該貨幣對。"
            )
        else:
            price = float(hist.iloc[-1]["Close"])

        day = date.today().strftime("%Y-%m-%d")
        if not hist.empty:
            day = hist.index[-1].strftime("%Y-%m-%d")

        return (
            f"💱 {item['name']}匯率參考\n"
            f"━━━━━━━━━━━━━━\n"
            f"匯率：{item['direction']}\n"
            f"1 單位外幣 ≈ {price:.4f} 新台幣\n"
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
