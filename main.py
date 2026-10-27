
import os
import re
import math
import logging
from datetime import datetime, timedelta

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
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not TOKEN or not SECRET:
    raise RuntimeError("請設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=TOKEN)
handler = WebhookHandler(SECRET)

HEADERS = {"User-Agent": "Mozilla/5.0"}


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


def get_stock_data(symbol):
    ticker = normalize_ticker(symbol)
    stock = yf.Ticker(ticker)
    history = stock.history(period="5d")

    if history.empty or history["Close"].dropna().empty:
        raise ValueError("找不到行情，請確認代碼或稍後再試。")

    price = float(history["Close"].dropna().iloc[-1])

    try:
        info = stock.info or {}
    except Exception:
        info = {}

    name = info.get("shortName") or info.get("longName") or ticker
    currency = info.get("currency") or (
        "TWD" if ticker.endswith(".TW") else "USD"
    )

    previous = number(
        info.get("regularMarketPreviousClose")
        or info.get("previousClose")
    )

    if previous is None and len(history["Close"].dropna()) >= 2:
        previous = float(history["Close"].dropna().iloc[-2])

    return {
        "ticker": ticker,
        "stock": stock,
        "info": info,
        "name": name,
        "price": price,
        "currency": currency,
        "previous": previous,
    }


def query_stock(symbol):
    data = get_stock_data(symbol)
    previous = data["previous"]

    if previous and previous > 0:
        change = data["price"] - previous
        pct = change / previous * 100
        change_text = f"{change:+.2f}（{pct:+.2f}%）"
    else:
        change_text = "暫無漲跌資料"

    return (
        f"📈 {data['name']}\n"
        f"代碼：{data['ticker']}\n"
        f"最新可取得收盤價：{data['price']:.2f} {data['currency']}\n"
        f"漲跌：{change_text}\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 報價可能延遲。"
    )


def query_valuation(symbol):
    data = get_stock_data(symbol)
    info = data["info"]

    eps = number(info.get("trailingEps"))
    pe = number(info.get("trailingPE"))
    forward_pe = number(info.get("forwardPE"))

    # Yahoo 基本面欄位取不到時，嘗試財報
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
                    values = [v for v in values if v is not None]

                    if len(values) >= 4:
                        eps = sum(values[:4])
                        break
        except Exception:
            app.logger.exception("EPS 查詢失敗")

    if pe is None and eps is not None and eps > 0:
        pe = data["price"] / eps

    eps_text = f"{eps:.2f}" if eps is not None else "暫無資料"

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

    return (
        f"📊 {data['name']}｜估值資訊\n"
        f"代碼：{data['ticker']}\n"
        f"股價：{data['price']:.2f} {data['currency']}\n"
        f"本益比：{pe_text}\n"
        f"預估本益比：{forward_text}\n"
        f"EPS：{eps_text}\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 部分財務資料可能無法取得。"
    )


def query_history(symbol, period="1mo"):
    allowed = {
        "1mo": "1mo",
        "3mo": "3mo",
        "6mo": "6mo",
        "1y": "1y",
    }
    period = allowed.get(period, "1mo")
    ticker = normalize_ticker(symbol)

    history = yf.Ticker(ticker).history(period=period)

    if history.empty:
        raise ValueError("查不到歷史股價。")

    history = history.dropna(subset=["Close"]).tail(5)
    lines = []

    for date, row in history.iterrows():
        lines.append(
            f"{date.strftime('%Y-%m-%d')}："
            f"{float(row['Close']):.2f}"
        )

    return (
        f"📅 {ticker} 歷史收盤價（{period}）\n"
        + "\n".join(lines)
        + "\n\n※ 顯示最近五筆資料，報價可能延遲。"
    )


def query_market(symbol, title):
    history = yf.Ticker(symbol).history(period="5d")

    if history.empty or history["Close"].dropna().empty:
        raise ValueError(f"{title}目前無法取得行情。")

    closes = history["Close"].dropna()
    price = float(closes.iloc[-1])

    if len(closes) >= 2:
        previous = float(closes.iloc[-2])
        change = price - previous
        pct = change / previous * 100 if previous else 0
        change_text = f"{change:+.4f}（{pct:+.2f}%）"
    else:
        change_text = "暫無漲跌資料"

    return (
        f"🌐 {title}\n"
        f"代碼：{symbol}\n"
        f"最新可取得價格：{price:,.4f}\n"
        f"漲跌：{change_text}\n\n"
        "資料來源：Yahoo Finance\n"
        "※ 黃金為國際黃金期貨報價，非台灣銀樓牌價。"
    )


def query_institutional(symbol):
    # TWSE 上市股票三大法人買賣超
    if not re.fullmatch(r"\d{4,6}", symbol):
        raise ValueError("三大法人查詢請輸入台股數字代碼，例如：籌碼 2330")

    today = datetime.now()
    last_error = None

    # 週末或資料尚未更新時，往前尋找最近可用交易日
    for offset in range(7):
        day = today - timedelta(days=offset)
        date_text = day.strftime("%Y%m%d")

        url = "https://www.twse.com.tw/rwd/zh/fund/T86"
        params = {
            "date": date_text,
            "selectType": "ALLBUT0999",
            "response": "json",
        }

        try:
            response = requests.get(
                url, params=params, headers=HEADERS, timeout=12
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
                    if all(word in field for word in keywords):
                        if i < len(match):
                            return match[i]
                return "無資料"

            foreign = find_value(["外陸資買賣超股數"])
            trust = find_value(["投信買賣超股數"])
            dealer = find_value(["自營商買賣超股數"])

            return (
                f"🏦 {symbol} 三大法人買賣超\n"
                f"資料日期：{day.strftime('%Y-%m-%d')}\n"
                f"外資：{foreign} 股\n"
                f"投信：{trust} 股\n"
                f"自營商：{dealer} 股\n\n"
                "資料來源：臺灣證券交易所\n"
                "※ 僅查詢上市股票；上櫃股票需使用櫃買中心資料。"
            )

        except Exception as exc:
            last_error = exc
            app.logger.warning("三大法人查詢失敗：%s", exc)

    raise ValueError(
        "目前查不到這檔股票的三大法人資料，"
        "可能是非交易日、資料尚未公布或資料來源暫時無法使用。"
    )


def help_text():
    return (
        "📊 川投顧｜功能說明\n\n"
        "【股票】\n"
        "2330、0050、AAPL\n\n"
        "【估值】\n"
        "本益比 2330\n"
        "EPS AAPL\n\n"
        "【歷史股價】\n"
        "歷史 2330\n"
        "歷史 AAPL 3mo\n"
        "期間可用：1mo、3mo、6mo、1y\n\n"
        "【三大法人】\n"
        "籌碼 2330\n\n"
        "【國際行情】\n"
        "黃金\n"
        "匯率 或 USDTWD\n"
        "USDJPY\n"
        "BTC 或 比特幣\n\n"
        "資料僅供參考，不構成投資建議。"
    )


def handle_command(message):
    message = message.strip()
    if not message:
        return help_text()

    parts = message.split()
    command = parts[0].upper()

    if command in ("說明", "HELP", "功能"):
        return help_text()

    if command in ("本益比", "估值", "EPS"):
        if len(parts) < 2:
            return f"請輸入代碼，例如：{parts[0]} 2330"
        return query_valuation(parts[1])

    if command in ("歷史", "HISTORY"):
        if len(parts) < 2:
            return "格式：歷史 2330 1mo"
        period = parts[2] if len(parts) >= 3 else "1mo"
        return query_history(parts[1], period)

    if command in ("籌碼", "三大法人"):
        if len(parts) < 2:
            return "格式：籌碼 2330"
        return query_institutional(parts[1])

    if command in ("黃金", "GOLD"):
        return query_market("GC=F", "國際黃金期貨")

    if command in ("匯率", "USDTWD", "USDTWD=X"):
        return query_market("USDTWD=X", "美元兌新台幣")

    if command in ("USDJPY", "USDJPY=X"):
        return query_market("USDJPY=X", "美元兌日圓")

    if command in ("BTC", "比特幣", "BTC-USD"):
        return query_market("BTC-USD", "比特幣")

    if len(parts) == 1:
        return query_stock(parts[0])

    return help_text()


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
        reply = handle_command(event.message.text or "")
    except Exception as exc:
        app.logger.exception("處理查詢時發生錯誤")
        reply = f"查詢失敗：{str(exc)[:300]}"

    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply[:4500])],
            )
        )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
