
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
# 三大法人買賣超
# =========================

def query_institutional(symbol):
    if not re.fullmatch(r"\d{4,6}", symbol):
        raise ValueError(
            "三大法人查詢請輸入台股數字代碼，例如：籌碼 2330"
        )

    today = datetime.now()

    for offset in range(7):
        day = today - timedelta(days=offset)

        params = {
            "date": day.strftime("%Y%m%d"),
            "selectType": "ALLBUT0999",
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

            code_index = next(
                (
                    i for i, field in enumerate(fields)
                    if "證券代號" in field
                ),
                0,
            )

            match = next(
                (
                    row for row in rows
                    if len(row) > code_index
                    and str(row[code_index]).strip() == symbol
                ),
                None,
            )

            if not match:
                continue

            def find_value(keywords):
                for i, field in enumerate(fields):
                    if (
                        all(word in field for word in keywords)
                        and i < len(match)
                    ):
                        return match[i]

                return "無資料"

            foreign = find_value(["外陸資買賣超股數"])
            trust = find_value(["投信買賣超股數"])
            dealer = find_value(["自營商買賣超股數"])

            return (
                f"🏦 {symbol} 三大法人買賣超\n"
                f"資料日期：{day:%Y-%m-%d}\n"
                f"外資：{foreign} 股\n"
                f"投信：{trust} 股\n"
                f"自營商：{dealer} 股\n\n"
                "資料來源：臺灣證券交易所\n"
                "※ 僅查詢上市股票；上櫃股票需使用櫃買中心資料。"
            )

        except Exception as exc:
            app.logger.warning(
                "三大法人查詢失敗：%s", exc
            )

    raise ValueError(
        "目前查不到三大法人資料，可能是非交易日、"
        "資料尚未公布或資料來源暫時無法使用。"
    )


# =========================
# 功能說明
# =========================

def help_text():
    return (
        "📊 川投顧｜功能說明\n\n"
        "【即時股價與期間漲跌幅】\n"
        "輸入 2330、0050、AAPL\n"
        "查詢後會出現期間選單。\n"
        "期間：當天、一週、一個月、三個月、"
        "年初至今、自訂期間。\n\n"
        "【本益比與 EPS】\n"
        "本益比 2330\n"
        "EPS AAPL\n\n"
        "【三大法人】\n"
        "籌碼 2330\n\n"
        "【國際行情】\n"
        "黃金\n"
        "匯率 或 美元\n"
        "USDJPY\n"
        "BTC 或 比特幣\n\n"
        "【自訂期間】\n"
        "自訂 2330 2026-01-01 2026-03-31\n\n"
        "【完整功能說明】\n"
        "說明\n\n"
        "資料僅供參考，不構成投資建議。"
    )


# =========================
# 指令處理
# =========================

def handle_command(message):
    message = message.strip()

    if not message:
        return help_text(), None

    parts = message.split()
    command = parts[0].upper()

    if command in ("說明", "HELP", "功能", "完整功能選單"):
        return help_text(), None

    # 點擊期間選單
    if command in ("期間", "PERIOD"):
        if len(parts) < 3:
            return "請重新輸入股票代碼並選擇期間。", None

        symbol = parts[1]
        label = " ".join(parts[2:])

        if label == "自訂期間":
            return (
                f"請輸入起訖日期：\n"
                f"自訂 {symbol} YYYY-MM-DD YYYY-MM-DD\n"
                f"例如：自訂 {symbol} 2026-01-01 2026-03-31",
                None,
            )

        return query_period(symbol, label), None

    # 自訂日期
    if command in ("自訂期間", "自訂"):
        if len(parts) == 2:
            symbol = parts[1]
            return (
                f"請輸入起訖日期：\n"
                f"自訂 {symbol} YYYY-MM-DD YYYY-MM-DD\n"
                f"例如：自訂 {symbol} 2026-01-01 2026-03-31",
                None,
            )

        if len(parts) == 4:
            return (
                query_custom_period(
                    parts[1], parts[2], parts[3]
                ),
                None,
            )

        return (
            "格式：自訂 2330 2026-01-01 2026-03-31",
            None,
        )

    # 本益比與 EPS
    if command in ("本益比", "估值", "EPS"):
        if len(parts) < 2:
            return f"請輸入代碼，例如：{parts[0]} 2330", None

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

    # 三大法人
    if command in ("籌碼", "三大法人"):
        if len(parts) < 2:
            return "格式：籌碼 2330", None

        return query_institutional(parts[1]), None

    # 黃金
    if command in ("黃金", "GOLD"):
        return query_market(
            "GC=F", "國際黃金期貨"
        ), None

    # 美元匯率
    if command in (
        "匯率", "美元", "USDTWD", "USDTWD=X"
    ):
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

    # 單獨輸入代碼：先顯示股價，再附期間按鈕
    if len(parts) == 1:
        return (
            query_stock(parts[0]),
            stock_period_quick_reply(parts[0]),
        )

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
