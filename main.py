
import os
import re
import math
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


# =========================
# Flask 與 LINE 基本設定
# =========================

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not TOKEN or not SECRET:
    raise RuntimeError(
        "請設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET"
    )

configuration = Configuration(access_token=TOKEN)
handler = WebhookHandler(SECRET)

HEADERS = {"User-Agent": "Mozilla/5.0"}


# =========================
# 共用工具
# =========================

def normalize_ticker(symbol):
    symbol = symbol.strip().upper()

    # 台股與 ETF，例如 2330、0050、00937B
    if re.fullmatch(r"\d{4,6}[A-Z]?", symbol):
        return symbol + ".TW"

    return symbol


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def currency_for(ticker):
    return "TWD" if ticker.endswith(".TW") else "USD"


def get_stock_data(symbol):
    ticker = normalize_ticker(symbol)
    stock = yf.Ticker(ticker)

    history = stock.history(period="5d", auto_adjust=False)

    if history.empty or history["Close"].dropna().empty:
        raise ValueError("找不到行情，請確認代碼或稍後再試。")

    closes = history["Close"].dropna()
    price = float(closes.iloc[-1])

    try:
        info = stock.info or {}
    except Exception:
        info = {}

    name = (
        info.get("shortName")
        or info.get("longName")
        or ticker
    )

    currency = info.get("currency") or currency_for(ticker)

    previous = number(
        info.get("regularMarketPreviousClose")
        or info.get("previousClose")
    )

    if previous is None and len(closes) >= 2:
        previous = float(closes.iloc[-2])

    return {
        "ticker": ticker,
        "stock": stock,
        "info": info,
        "name": name,
        "price": price,
        "currency": currency,
        "previous": previous,
    }


# =========================
# 即時股價
# =========================

def query_stock(symbol):
    data = get_stock_data(symbol)
    previous = data["previous"]

    if previous is not None and previous > 0:
        change = data["price"] - previous
        pct = change / previous * 100
        change_text = f"{change:+.2f}（{pct:+.2f}%）"
    else:
        change_text = "暫無漲跌資料"

    return (
        f"📈 {data['name']}\n"
        f"代碼：{data['ticker']}\n"
        f"最新可取得價格："
        f"{data['price']:.2f} {data['currency']}\n"
        f"當日漲跌：{change_text}\n\n"
        "👇 請選擇下方按鈕，查詢不同期間的漲跌幅。\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 報價可能延遲，不一定是即時成交價。"
    )


def stock_period_quick_reply(symbol):
    items = []

    periods = [
        ("當天", "當天"),
        ("一週", "一週"),
        ("一個月", "一個月"),
        ("三個月", "三個月"),
        ("年初至今", "年初至今"),
        ("自訂期間", "自訂期間"),
    ]

    for label, period in periods:
        if period == "自訂期間":
            text = f"自訂期間 {symbol}"
        else:
            text = f"期間 {symbol} {period}"

        items.append(
            QuickReplyItem(
                action=MessageAction(
                    label=label,
                    text=text
                )
            )
        )

    return QuickReply(items=items)


# =========================
# 期間漲跌幅
# =========================

def query_period(symbol, label):
    ticker = normalize_ticker(symbol)
    stock = yf.Ticker(ticker)
    today = date.today()

    if label == "當天":
        history = stock.history(
            period="5d",
            auto_adjust=False
        )

        if history.empty:
            raise ValueError("目前查不到股價資料。")

        closes = history["Close"].dropna()

        if len(closes) < 2:
            raise ValueError(
                "目前缺少足夠的收盤價，無法計算當日漲跌幅。"
            )

        start_price = float(closes.iloc[-2])
        end_price = float(closes.iloc[-1])

        start_date = closes.index[-2].strftime("%Y-%m-%d")
        end_date = closes.index[-1].strftime("%Y-%m-%d")

    else:
        if label == "一週":
            history = stock.history(
                period="5d",
                auto_adjust=False
            )

        elif label == "一個月":
            history = stock.history(
                period="1mo",
                auto_adjust=False
            )

        elif label == "三個月":
            history = stock.history(
                period="3mo",
                auto_adjust=False
            )

        elif label == "年初至今":
            history = stock.history(
                start=f"{today.year}-01-01",
                end=(today + timedelta(days=1)).isoformat(),
                auto_adjust=False
            )

        else:
            raise ValueError("不支援的查詢期間。")

        if history.empty:
            raise ValueError("查不到這段期間的歷史股價。")

        closes = history["Close"].dropna()

        if closes.empty:
            raise ValueError("目前沒有可用的收盤價。")

        start_price = float(closes.iloc[0])
        end_price = float(closes.iloc[-1])

        start_date = closes.index[0].strftime("%Y-%m-%d")
        end_date = closes.index[-1].strftime("%Y-%m-%d")

    change = end_price - start_price
    pct = change / start_price * 100 if start_price else 0
    currency = currency_for(ticker)

    return (
        f"📊 {ticker}｜{label}漲跌幅\n"
        f"期間：{start_date} ～ {end_date}\n"
        f"起始收盤價：{start_price:.2f} {currency}\n"
        f"最新收盤價：{end_price:.2f} {currency}\n"
        f"漲跌金額：{change:+.2f} {currency}\n"
        f"漲跌幅：{pct:+.2f}%\n\n"
        "計算方式：以區間第一筆可用收盤價"
        "與最後一筆可用收盤價比較。\n"
        "資料來源：Yahoo Finance\n"
        "※ 報價可能延遲。"
    )


def query_custom_period(symbol, start_text, end_text):
    try:
        start_day = datetime.strptime(
            start_text, "%Y-%m-%d"
        ).date()

        end_day = datetime.strptime(
            end_text, "%Y-%m-%d"
        ).date()

    except ValueError:
        raise ValueError(
            "日期格式錯誤，請使用 YYYY-MM-DD。\n"
            "例如：自訂 2330 2026-01-01 2026-03-31"
        )

    if start_day > end_day:
        raise ValueError("起始日期不能晚於結束日期。")

    if end_day > date.today():
        raise ValueError("結束日期不能晚於今天。")

    ticker = normalize_ticker(symbol)

    history = yf.Ticker(ticker).history(
        start=start_day.isoformat(),
        end=(end_day + timedelta(days=1)).isoformat(),
        auto_adjust=False
    )

    if history.empty:
        raise ValueError(
            "這段日期沒有可用的股價資料，請確認日期或交易日。"
        )

    closes = history["Close"].dropna()

    if closes.empty:
        raise ValueError("這段日期沒有可用的收盤價。")

    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])

    change = last - first
    pct = change / first * 100 if first else 0
    currency = currency_for(ticker)

    return (
        f"📊 {ticker}｜自訂期間漲跌幅\n"
        f"指定期間：{start_day} ～ {end_day}\n"
        f"起始可用收盤價"
        f"（{closes.index[0].strftime('%Y-%m-%d')}）："
        f"{first:.2f} {currency}\n"
        f"最新可用收盤價"
        f"（{closes.index[-1].strftime('%Y-%m-%d')}）："
        f"{last:.2f} {currency}\n"
        f"漲跌金額：{change:+.2f} {currency}\n"
        f"漲跌幅：{pct:+.2f}%\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 以指定期間內第一筆與最後一筆可用收盤價計算。"
    )


# =========================
# 本益比與 EPS
# =========================

def query_valuation(symbol):
    data = get_stock_data(symbol)
    info = data["info"]

    eps = number(info.get("trailingEps"))
    pe = number(info.get("trailingPE"))

    forward_pe = number(info.get("forwardPE"))
    forward_eps = number(info.get("forwardEps"))

    # Yahoo 沒提供 EPS 時，嘗試從季度財報加總
    if eps is None:
        try:
            statement = data["stock"].quarterly_income_stmt

            if statement is not None and not statement.empty:
                rows = {
                    str(index).strip().lower(): index
                    for index in statement.index
                }

                for label in ("diluted eps", "basic eps"):
                    row = rows.get(label)

                    if row is None:
                        continue

                    values = [
                        number(v)
                        for v in statement.loc[row].tolist()
                    ]

                    values = [
                        v for v in values if v is not None
                    ]

                    if len(values) >= 4:
                        eps = sum(values[:4])
                        break

        except Exception:
            app.logger.exception("EPS 查詢失敗")

    # 缺少本益比時，以股價 / 過去 EPS 計算
    if pe is None and eps is not None and eps > 0:
        pe = data["price"] / eps

    # 缺少預估本益比時，以股價 / 預估 EPS 計算
    if (
        forward_pe is None
        and forward_eps is not None
        and forward_eps > 0
    ):
        forward_pe = data["price"] / forward_eps

    eps_text = (
        f"{eps:.2f}" if eps is not None else "暫無資料"
    )

    if eps is not None and eps <= 0:
        pe_text = "不適用（EPS 非正數）"
    elif pe is not None:
        pe_text = f"{pe:.2f} 倍"
    else:
        pe_text = "暫無資料"

    forward_text = (
        f"{forward_pe:.2f} 倍"
        if forward_pe is not None
        else "暫無預估資料"
    )

    forward_eps_text = (
        f"{forward_eps:.2f}"
        if forward_eps is not None
        else "暫無資料"
    )

    return (
        f"📊 {data['name']}｜估值資訊\n"
        f"代碼：{data['ticker']}\n"
        f"股價：{data['price']:.2f} {data['currency']}\n"
        f"本益比：{pe_text}\n"
        f"預估本益比：{forward_text}\n"
        f"過去 EPS：{eps_text}\n"
        f"預估 EPS：{forward_eps_text}\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 預估資料可能缺漏，估值僅供參考。"
    )


# =========================
# 國際行情：黃金、匯率、比特幣
# =========================

def query_market(symbol, title):
    history = yf.Ticker(symbol).history(period="5d")

    if history.empty or history["Close"].dropna().empty:
        raise ValueError(f"{title}目前無法取得行情。")

    closes = history["Close"].dropna()
    price = float(closes.iloc[-1])

    if len(closes) >= 2:
        previous = float(closes.iloc[-2])
        change = price - previous

        if previous:
            change_text = (
                f"{change:+.4f}（{change / previous * 100:+.2f}%）"
            )
        else:
            change_text = "暫無漲跌資料"
    else:
        change_text = "暫無漲跌資料"

    if symbol == "USDTWD=X":
        currency = "TWD / USD"
    elif symbol == "USDJPY=X":
        currency = "JPY / USD"
    elif symbol == "BTC-USD":
        currency = "USD"
    else:
        currency = "USD / 金衡盎司"

    return (
        f"🌐 {title}\n"
        f"代碼：{symbol}\n"
        f"最新可取得價格：{price:,.4f} {currency}\n"
        f"漲跌：{change_text}\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 報價可能延遲。黃金為國際黃金期貨，"
        "非台灣銀樓牌價。"
    )

# =========================
# 三大法人 + 散戶參考籌碼
# =========================

def query_institutional(symbol):
    if not re.fullmatch(r"\d{4,6}", symbol):
        raise ValueError(
            "請輸入台股數字代碼，例如：籌碼 2330"
        )

    def to_int(value):
        """將 API 回傳的數字轉成整數。"""
        try:
            return int(float(str(value).replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0

    def fmt(value):
        """顯示股數並加上千分位。"""
        return f"{value:+,}" if value != 0 else "0"

    def get_field(fields, row, field_name):
        """依照完整欄位名稱取值，避免抓錯欄位。"""
        try:
            index = fields.index(field_name)
            return to_int(row[index])
        except (ValueError, IndexError):
            return 0

    today = datetime.now()

    # ---------------------------------
    # 1. 查詢證交所上市股票三大法人
    # ---------------------------------
    institutional_text = None
    latest_day = None

    for offset in range(10):
        day = today - timedelta(days=offset)

        params = {
            "date": day.strftime("%Y%m%d"),
            "selectType": "ALL",
            "response": "json",
        }

        try:
            response = requests.get(
                "https://www.twse.com.tw/rwd/zh/fund/T86",
                params=params,
                headers=HEADERS,
                timeout=12,
            )
            response.raise_for_status()
            result = response.json()

            if result.get("stat") != "OK":
                continue

            fields = result.get("fields", [])
            rows = result.get("data", [])

            code_index = fields.index("證券代號")

            match = next(
                (
                    row for row in rows
                    if len(row) > code_index
                    and str(row[code_index]).strip() == symbol
                ),
                None,
            )

            if match is None:
                continue

            # 外資（不含外資自營商）
            foreign_buy = get_field(
                fields, match,
                "外陸資買進股數(不含外資自營商)"
            )
            foreign_sell = get_field(
                fields, match,
                "外陸資賣出股數(不含外資自營商)"
            )
            foreign_net = get_field(
                fields, match,
                "外陸資買賣超股數(不含外資自營商)"
            )

            # 外資自營商：併入外資，避免漏算
            foreign_dealer_buy = get_field(
                fields, match, "外資自營商買進股數"
            )
            foreign_dealer_sell = get_field(
                fields, match, "外資自營商賣出股數"
            )
            foreign_dealer_net = get_field(
                fields, match, "外資自營商買賣超股數"
            )

            foreign_buy += foreign_dealer_buy
            foreign_sell += foreign_dealer_sell
            foreign_net += foreign_dealer_net

            # 投信
            trust_buy = get_field(
                fields, match, "投信買進股數"
            )
            trust_sell = get_field(
                fields, match, "投信賣出股數"
            )
            trust_net = get_field(
                fields, match, "投信買賣超股數"
            )

            # 自營商：自行買賣 + 避險
            dealer_buy = (
                get_field(
                    fields, match,
                    "自營商買進股數(自行買賣)"
                )
                + get_field(
                    fields, match,
                    "自營商買進股數(避險)"
                )
            )
            dealer_sell = (
                get_field(
                    fields, match,
                    "自營商賣出股數(自行買賣)"
                )
                + get_field(
                    fields, match,
                    "自營商賣出股數(避險)"
                )
            )
            dealer_net = get_field(
                fields, match, "自營商買賣超股數"
            )

            # 三大法人合計
            total_buy = foreign_buy + trust_buy + dealer_buy
            total_sell = foreign_sell + trust_sell + dealer_sell

            # 優先使用證交所公布的合計淨額
            total_net = get_field(
                fields, match, "三大法人買賣超股數"
            )

            latest_day = day
            institutional_text = (
                f"🏦 {symbol}｜三大法人籌碼\n"
                f"資料日期：{day:%Y-%m-%d}\n"
                f"單位：股\n\n"
                f"【外資】\n"
                f"買進：{foreign_buy:,} 股\n"
                f"賣出：{foreign_sell:,} 股\n"
                f"買賣超：{fmt(foreign_net)} 股\n\n"
                f"【投信】\n"
                f"買進：{trust_buy:,} 股\n"
                f"賣出：{trust_sell:,} 股\n"
                f"買賣超：{fmt(trust_net)} 股\n\n"
                f"【自營商】\n"
                f"買進：{dealer_buy:,} 股\n"
                f"賣出：{dealer_sell:,} 股\n"
                f"買賣超：{fmt(dealer_net)} 股\n\n"
                f"📌【三大法人合計】\n"
                f"總買進：{total_buy:,} 股\n"
                f"總賣出：{total_sell:,} 股\n"
                f"總買賣超：{fmt(total_net)} 股\n"
            )
            break

        except Exception as exc:
            app.logger.warning(
                "證交所法人資料查詢失敗：%s", exc
            )

    if institutional_text is None:
        raise ValueError(
            "目前查不到上市股票法人資料。"
            "請確認代碼、交易日期或稍後再試。"
        )

    # ---------------------------------
    # 2. 查詢融資融券（散戶參考指標）
    #    使用 FinMind API V4
    # ---------------------------------
    margin_text = "\n👥【散戶參考指標｜融資融券】\n"

    finmind_token = os.getenv("FINMIND_TOKEN")

    if not finmind_token:
        margin_text += (
            "尚未設定 FINMIND_TOKEN，"
            "暫時無法取得融資融券資料。\n"
        )
    else:
        try:
            margin_response = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={
                    "dataset": "TaiwanStockMarginPurchaseShortSale",
                    "data_id": symbol,
                    "start_date": (
                        latest_day - timedelta(days=15)
                    ).strftime("%Y-%m-%d"),
                    "end_date": latest_day.strftime("%Y-%m-%d"),
                },
                headers={
                    "Authorization": f"Bearer {finmind_token}"
                },
                timeout=15,
            )
            margin_response.raise_for_status()
            margin_result = margin_response.json()

            if margin_result.get("status") != 200:
                raise ValueError(
                    margin_result.get("msg", "FinMind 回傳錯誤")
                )

            records = margin_result.get("data", [])

            if not records:
                margin_text += "目前沒有可用的融資融券資料。\n"
            else:
                records.sort(key=lambda item: item["date"])
                current = records[-1]

                previous = (
                    records[-2] if len(records) >= 2 else None
                )

                margin_balance = to_int(
                    current.get("MarginPurchaseTodayBalance")
                )
                short_balance = to_int(
                    current.get("ShortSaleTodayBalance")
                )

                margin_change = (
                    margin_balance - to_int(
                        previous.get("MarginPurchaseTodayBalance")
                    )
                    if previous else None
                )
                short_change = (
                    short_balance - to_int(
                        previous.get("ShortSaleTodayBalance")
                    )
                    if previous else None
                )

                margin_text += (
                    f"資料日期：{current['date']}\n"
                    f"融資買進："
                    f"{to_int(current.get('MarginPurchaseBuy')):,} 張\n"
                    f"融資賣出："
                    f"{to_int(current.get('MarginPurchaseSell')):,} 張\n"
                    f"融資餘額：{margin_balance:,} 張\n"
                    f"融資餘額變化："
                    f"{fmt(margin_change) if margin_change is not None else '暫無比較資料'} 張\n\n"
                    f"融券買進："
                    f"{to_int(current.get('ShortSaleBuy')):,} 張\n"
                    f"融券賣出："
                    f"{to_int(current.get('ShortSaleSell')):,} 張\n"
                    f"融券餘額：{short_balance:,} 張\n"
                    f"融券餘額變化："
                    f"{fmt(short_change) if short_change is not None else '暫無比較資料'} 張\n"
                )

        except Exception:
            app.logger.exception("FinMind 融資融券查詢失敗")
            margin_text += (
                "目前無法取得融資融券資料，"
                "請稍後再試。\n"
            )

    return (
        institutional_text
        + margin_text
        + "\n資料來源：臺灣證券交易所、FinMind\n"
        "※ 融資融券是市場籌碼參考指標，"
        "不代表所有散戶的完整買賣超。"
        "以上資料僅供參考，不構成投資建議。"
    )




# =========================
# 功能說明
# =========================

def help_text():
    return (
        "📈【川投顧｜功能選單】\n\n"
            "歡迎使用川投顧！💹\n"
            "輸入以下指令即可查詢金融資訊。\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "📊【1. 即時股價查詢】\n"
            "直接輸入股票代碼：\n"
            "▪️ 2330（台積電）\n"
            "▪️ 0050（元大台灣50）\n"
            "▪️ AAPL（Apple）\n\n"
            "🏦【2. 三大法人籌碼】\n"
            "指令：籌碼 股票代碼\n"
            "範例：籌碼 2330\n\n"
            "💰【3. 本益比與 EPS】\n"
            "指令：本益比 股票代碼\n"
            "範例：本益比 2330\n"
            "也可輸入：EPS AAPL\n\n"
            "📉【4. 歷史區間漲跌】\n"
            "▪️ 期間 2330 當天\n"
            "▪️ 期間 2330 一週\n"
            "▪️ 期間 2330 一個月\n"
            "▪️ 期間 2330 三個月\n"
            "▪️ 期間 2330 年初至今\n\n"
            "🗓️【5. 自訂日期區間】\n"
            "第一步：輸入「自定區間」\n"
            "第二步：依照提示輸入股票代碼及日期。\n"
            "格式：自訂 2330 2026-01-01 2026-03-31\n\n"
            "🌏【6. 國際金融行情】\n"
            "▪️ 黃金\n"
            "▪️ 匯率（美元兌新台幣）\n"
            "▪️ USDJPY（美元兌日圓）\n"
            "▪️ BTC（比特幣）\n\n"
            "━━━━━━━━━━━━━━\n"
            "💡 指令與參數之間請用空格分隔。\n"
            "⚠️ 金融資訊僅供參考，不構成投資建議。",
            None,
    )



# =========================
# 指令處理
# =========================

def handle_command(message):
    message = message.strip()

    if not message:
        return ("查詢失敗：找不到行情，請確認代碼或稍後再試。"), None

    parts = message.split()
    command = parts[0].upper()
def handle_command(text):
    parts = text.strip().split()
    command = parts[0] if parts else ""

    # 直接輸入股票代碼 + 起始日期 + 結束日期
    if (
        len(parts) == 3
        and re.fullmatch(r"[A-Za-z0-9.^=-]+", parts[0])
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[2])
    ):
        symbol = parts[0].upper()
        start_date = parts[1]
        end_date = parts[2]

        return query_custom_period(
            symbol,
            start_date,
            end_date
        ), None

    # 功能說明
    if command in ["功能選單", "功能", "HELP", "說明"]:
        return help_text(), None
    
    # 下面保留你原本的其他指令判斷
    # 功能選單
  
    if command in ["功能選單", "功能", "HELP", "說明"]:
        return (
            "📈【川投顧｜功能選單】\n\n"
            "歡迎使用川投顧！💹\n"
            "輸入以下指令即可查詢金融資訊。\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "📊【1. 即時股價查詢】\n"
            "直接輸入股票代碼：\n"
            "▪️ 2330（台積電）\n"
            "▪️ 0050（元大台灣50）\n"
            "▪️ AAPL（Apple）\n\n"
            "🏦【2. 三大法人籌碼】\n"
            "指令：籌碼 股票代碼\n"
            "範例：籌碼 2330\n\n"
            "💰【3. 本益比與 EPS】\n"
            "指令：本益比 股票代碼\n"
            "範例：本益比 2330\n"
            "也可輸入：EPS AAPL\n\n"
            "📉【4. 歷史區間漲跌】\n"
            "▪️ 期間 2330 當天\n"
            "▪️ 期間 2330 一週\n"
            "▪️ 期間 2330 一個月\n"
            "▪️ 期間 2330 三個月\n"
            "▪️ 期間 2330 年初至今\n\n"
            "🗓️【5. 自訂日期區間】\n"
            "第一步：輸入「自定區間」\n"
            "第二步：依照提示輸入股票代碼及日期。\n"
            "格式：自訂 2330 2026-01-01 2026-03-31\n\n"
            "🌏【6. 國際金融行情】\n"
            "▪️ 黃金\n"
            "▪️ 匯率（美元兌新台幣）\n"
            "▪️ USDJPY（美元兌日圓）\n"
            "▪️ BTC（比特幣）\n\n"
            "━━━━━━━━━━━━━━\n"
            "💡 指令與參數之間請用空格分隔。\n"
            "⚠️ 金融資訊僅供參考，不構成投資建議。",
            None,
        )

    # 點擊股價下方的期間快速選單
    if command in ("期間", "PERIOD"):
        if len(parts) < 3:
            return (
                "請重新輸入期間指令。\n"
                "例如：期間 2330 一週",
                None,
            )

        symbol = parts[1]
        label = " ".join(parts[2:])

        if label == "自訂期間":
            return (
                "🗓️【自訂日期區間查詢】\n\n"
                "請輸入完整指令：\n"
                f"自訂 {symbol} YYYY-MM-DD YYYY-MM-DD\n\n"
                f"範例：自訂 {symbol} 2026-01-01 2026-03-31",
                None,
            )

        return query_period(symbol, label), None

    # 自訂日期區間
    if command in ["自訂期間", "自訂", "自定區間", "自訂區間"]:
        # 只輸入「自定區間」
        if len(parts) == 1:
            return (
                "🗓️【自訂日期區間查詢】\n\n"
                "請輸入股票代碼與起訖日期。\n\n"
                "格式：\n"
                "自訂 股票代碼 開始日期 結束日期\n\n"
                "範例：\n"
                "自訂 2330 2026-01-01 2026-03-31\n\n"
                "日期格式請使用 YYYY-MM-DD。",
                None,
            )

        # 只輸入指令及股票代碼
        if len(parts) == 2:
            symbol = parts[1]
            return (
                "🗓️【自訂日期區間查詢】\n\n"
                "請接著輸入開始日期與結束日期。\n\n"
                f"格式：自訂 {symbol} YYYY-MM-DD YYYY-MM-DD\n"
                f"範例：自訂 {symbol} 2026-01-01 2026-03-31",
                None,
            )

        # 完整格式：自訂 2330 2026-01-01 2026-03-31
        if len(parts) == 4:
            return (
                query_custom_period(
                    parts[1], parts[2], parts[3]
                ),
                None,
            )

        return (
            "❌ 指令格式錯誤！\n\n"
            "請使用：\n"
            "自訂 股票代碼 開始日期 結束日期\n\n"
            "範例：自訂 2330 2026-01-01 2026-03-31",
            None,
        )

    # 本益比與 EPS
    if command in ("本益比", "估值", "EPS"):
        if len(parts) < 2:
            return (
                f"請輸入股票代碼。\n"
                f"範例：{parts[0]} 2330",
                None,
            )

        return query_valuation(parts[1]), None

    # 歷史股價
    if command in ("歷史", "HISTORY"):
        if len(parts) < 2:
            return "格式：歷史 2330", None

        ticker = normalize_ticker(parts[1])
        history = yf.Ticker(ticker).history(
            period="1mo",
            auto_adjust=False,
        )

        if history.empty:
            raise ValueError("查不到歷史股價。")

        history = history.dropna(subset=["Close"]).tail(5)

        lines = [
            f"{d.strftime('%Y-%m-%d')}：{float(row['Close']):.2f}"
            for d, row in history.iterrows()
        ]

        return (
            f"📅 {ticker} 最近五筆收盤價\n"
            + "\n".join(lines),
            None,
        )

    # 三大法人籌碼
    if command in ("籌碼", "三大法人"):
        if len(parts) < 2:
            return "格式：籌碼 2330", None

        return query_institutional(parts[1]), None

    # 黃金
    if command in ("黃金", "GOLD"):
        return query_market(
            "GC=F", "國際黃金期貨"
        ), None

    # 美元兌新台幣
    if command in ("匯率", "美元", "USDTWD", "USDTWD=X"):
        return query_market(
            "USDTWD=X", "美元兌新台幣"
        ), None

    # 美元兌日圓
    if command in ("USDJPY", "USDJPY=X"):
        return query_market(
            "USDJPY=X", "美元兌日圓"
        ), None

    # 比特幣
    if command in ("BTC", "比特幣", "BTC-USD"):
        return query_market(
            "BTC-USD", "比特幣"
        ), None

    # 單獨輸入股票代碼：股價 + 期間按鈕
    if len(parts) == 1:
        return (
            query_stock(parts[0]),
            stock_period_quick_reply(parts[0]),
        )

    # 其他無法辨識的指令
    return help_text(), None

# =========================
# Flask 路由與 LINE Webhook
# =========================

@app.get("/")
def home():
    return "川投顧已啟動"


@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        reply, quick_reply = handle_command(
            event.message.text or ""
        )

        message = TextMessage(
            text=reply[:4500],
            quick_reply=quick_reply,
        )

    except Exception as exc:
        app.logger.exception("處理查詢時發生錯誤")

        message = TextMessage(
            text=f"查詢失敗：{str(exc)[:300]}"
        )

    with ApiClient(configuration) as client:
        api = MessagingApi(client)

        api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[message],
            )
        )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
