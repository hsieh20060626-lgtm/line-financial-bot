
import os
import logging
from datetime import datetime, timedelta

import requests
import yfinance as yf
from flask import Flask, abort, request

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    TemplateMessage,
    ButtonsTemplate,
    MessageAction,
    FlexMessage,
    FlexContainer,
)

# ==================================================
# 川投顧 LINE Bot
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("chuan_tougu")

app = Flask(__name__)

ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()

configuration = Configuration(access_token=ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


# ==================================================
# 共用函式
# ==================================================

def reply_text(reply_token, text):
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


def send_messages(reply_token, messages):
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages,
            )
        )


def finmind_query(dataset, stock_code, start_date=None):
    params = {
        "dataset": dataset,
        "data_id": stock_code,
    }

    if start_date:
        params["start_date"] = start_date

    response = requests.get(
        FINMIND_URL,
        params=params,
        timeout=15,
    )
    response.raise_for_status()

    result = response.json()

    if result.get("status") not in (None, 200, "200"):
        logger.warning("FinMind 回應：%s", result.get("msg"))

    return result.get("data", []) or []


def stock_tickers(stock_code):
    code = stock_code.strip().upper()

    if code.isdigit():
        return [f"{code}.TW", f"{code}.TWO"]

    return [code]


def is_number(value):
    try:
        number = float(value)
        return number == number and abs(number) != float("inf")
    except (TypeError, ValueError):
        return False


def is_positive_number(value):
    return is_number(value) and float(value) > 0


def get_stock_history(stock_code, period="1mo"):
    for symbol in stock_tickers(stock_code):
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(
                period=period,
                raise_errors=True,
            )

            if history is not None and not history.empty:
                return ticker, history, symbol

        except Exception as exc:
            logger.warning("查詢 %s 失敗：%s", symbol, exc)

    return None, None, None


def get_company_name(ticker, fallback):
    try:
        info = ticker.info or {}
        return (
            info.get("shortName")
            or info.get("longName")
            or fallback
        )
    except Exception as exc:
        logger.warning("取得公司名稱失敗：%s", exc)
        return fallback


# ==================================================
# 1. 本益比與 EPS
# ==================================================

def query_valuation(stock_code):
    company_name = stock_code
    pe = None
    eps = None
    data_date = None
    pbr = None
    dividend_yield = None

    # 台股：優先使用 FinMind
    if stock_code.isdigit():
        try:
            rows = finmind_query(
                "TaiwanStockPER",
                stock_code,
                (
                    datetime.now() - timedelta(days=45)
                ).strftime("%Y-%m-%d"),
            )

            rows.sort(key=lambda row: row.get("date", ""))

            if rows:
                latest = rows[-1]

                data_date = latest.get("date")
                pe = latest.get("PER")
                pbr = latest.get("PBR")
                dividend_yield = latest.get("dividend_yield")

        except Exception as exc:
            logger.exception("FinMind 本益比查詢失敗：%s", exc)

    # Yahoo Finance：公司名稱與 EPS 備援
    ticker = None

    for symbol in stock_tickers(stock_code):
        try:
            candidate = yf.Ticker(symbol)
            info = candidate.info or {}

            name = (
                info.get("shortName")
                or info.get("longName")
            )

            if name:
                company_name = name
                ticker = candidate

                eps = info.get("trailingEps")

                if not is_positive_number(pe):
                    pe = info.get("trailingPE")

                break

        except Exception as exc:
            logger.warning(
                "Yahoo 估值查詢 %s 失敗：%s",
                symbol,
                exc,
            )

    # 沒有 PE，但有正 EPS 時，才嘗試自行估算
    if not is_positive_number(pe) and ticker is not None:
        try:
            if not is_number(eps):
                eps = (ticker.info or {}).get("trailingEps")

            if is_positive_number(eps):
                history = ticker.history(period="5d")

                if history is not None and not history.empty:
                    close_price = float(history["Close"].iloc[-1])
                    pe = close_price / float(eps)

        except Exception as exc:
            logger.warning("本益比備援計算失敗：%s", exc)

    pe_text = (
        f"{float(pe):.2f} 倍"
        if is_positive_number(pe)
        else "無有效資料"
    )

    eps_text = (
        f"{float(eps):.2f}"
        if is_number(eps)
        else "無資料"
    )

    lines = [
        f"⚖️【{company_name} ({stock_code})】估值指標",
        "--------------------------------",
        f"🔹 本益比 (PE)：{pe_text}",
        f"🔹 每股盈餘 (EPS)：{eps_text}",
    ]

    if data_date:
        lines.append(f"📅 本益比資料日期：{data_date}")

    if is_number(pbr):
        lines.append(f"🔹 股價淨值比 (PBR)：{float(pbr):.2f}")

    if is_number(dividend_yield):
        lines.append(
            f"🔹 殖利率：{float(dividend_yield):.2f}%"
        )

    lines.extend([
        "--------------------------------",
        "※ 資料可能延遲或缺漏，僅供參考。",
    ])

    return "\n".join(lines)


# ==================================================
# 2. 三大法人籌碼
# ==================================================

def query_chips_and_institutional(stock_code):
    if not stock_code.isdigit():
        return "目前法人籌碼查詢僅支援台股數字代碼，例如：籌碼 2330"

    start_date = (
        datetime.now() - timedelta(days=20)
    ).strftime("%Y-%m-%d")

    try:
        company_name = stock_code

        for symbol in stock_tickers(stock_code):
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info or {}

                company_name = (
                    info.get("shortName")
                    or info.get("longName")
                    or stock_code
                )

                if company_name != stock_code:
                    break

            except Exception:
                continue

        records = finmind_query(
            "TaiwanStockInstitutionalInvestorsBuySell",
            stock_code,
            start_date,
        )

        if not records:
            return (
                f"查不到 {stock_code} 的法人買賣超資料。\n"
                "請稍後再試。"
            )

        latest_date = max(
            row.get("date", "") for row in records
        )

        day_records = [
            row for row in records
            if row.get("date") == latest_date
        ]

        foreign = 0
        trust = 0
        dealer = 0

        for row in day_records:
            name = str(row.get("name", ""))

            try:
                buy = float(row.get("buy", 0) or 0)
                sell = float(row.get("sell", 0) or 0)
            except (TypeError, ValueError):
                continue

            net = buy - sell

            if "Foreign" in name or "外資" in name:
                foreign += net
            elif "Investment_Trust" in name or "投信" in name:
                trust += net
            elif "Dealer" in name or "自營商" in name:
                dealer += net

        def format_lots(shares):
            lots = round(shares / 1000)

            if lots > 0:
                return f"買超 +{lots:,} 張"
            elif lots < 0:
                return f"賣超 {lots:,} 張"

            return "持平 0 張"

        total = foreign + trust + dealer

        return (
            f"📊【{company_name} ({stock_code})】法人買賣超\n"
            f"📅 資料日期：{latest_date}\n"
            "--------------------------------\n"
            f"🌍 外資：{format_lots(foreign)}\n"
            f"🏦 投信：{format_lots(trust)}\n"
            f"💼 自營商：{format_lots(dealer)}\n"
            f"📈 三大法人合計：{format_lots(total)}\n"
            "--------------------------------\n"
            "※ 數據單位及更新時間依資料來源為準。"
        )

    except Exception as exc:
        logger.exception("法人查詢失敗：%s", exc)
        return "法人籌碼查詢失敗，請稍後再試。"


# ==================================================
# 3. 股票時間區間查詢
# ==================================================

def build_stock_period_message(stock_code, period):
    period_names = {
        "2d": "近兩日",
        "1wk": "一週",
        "1mo": "一個月",
        "3mo": "三個月",
        "6mo": "六個月",
        "ytd": "年初至今",
    }

    period_name = period_names.get(period, period)

    ticker, history, symbol = get_stock_history(
        stock_code,
        period,
    )

    if ticker is None or history is None or history.empty:
        return TextMessage(
            text=f"找不到「{stock_code}」的股價資料，請確認代碼或稍後再試。"
        )

    if len(history) < 2:
        return TextMessage(
            text="目前取得的資料不足，無法計算區間漲跌幅。"
        )

    try:
        start_price = float(history["Close"].iloc[0])
        end_price = float(history["Close"].iloc[-1])

        if start_price == 0:
            raise ValueError("起始價格為零")

        change_amount = end_price - start_price
        change_pct = change_amount / start_price * 100

        latest_date = history.index[-1].strftime("%Y-%m-%d")
        company_name = get_company_name(ticker, stock_code)

        is_up = change_pct >= 0
        header_color = "#D93025" if is_up else "#188038"
        arrow = "📈" if is_up else "📉"

        sign_pct = "+" if change_pct > 0 else ""
        sign_amount = "+" if change_amount > 0 else ""

        flex_json = {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": header_color,
                "paddingAll": "lg",
                "contents": [
                    {
                        "type": "text",
                        "text": f"{arrow} {company_name} ({stock_code})",
                        "color": "#ffffff",
                        "weight": "bold",
                        "size": "lg",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": f"查詢區間：{period_name}",
                        "color": "#ffffff",
                        "size": "sm",
                        "margin": "xs",
                    },
                ],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "起始參考價",
                                "color": "#888888",
                                "size": "sm",
                            },
                            {
                                "type": "text",
                                "text": f"{start_price:.2f}",
                                "align": "end",
                                "size": "sm",
                                "weight": "bold",
                            },
                        ],
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "最新收盤價",
                                "color": "#888888",
                                "size": "sm",
                            },
                            {
                                "type": "text",
                                "text": f"{end_price:.2f}",
                                "align": "end",
                                "size": "sm",
                                "weight": "bold",
                            },
                        ],
                    },
                    {
                        "type": "separator",
                        "margin": "lg",
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "lg",
                        "contents": [
                            {
                                "type": "text",
                                "text": "區間漲跌幅",
                                "size": "md",
                                "weight": "bold",
                            },
                            {
                                "type": "text",
                                "text": f"{sign_pct}{change_pct:.2f}%",
                                "align": "end",
                                "size": "md",
                                "weight": "bold",
                                "color": header_color,
                            },
                        ],
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "sm",
                        "contents": [
                            {
                                "type": "text",
                                "text": "漲跌金額",
                                "color": "#666666",
                                "size": "xs",
                            },
                            {
                                "type": "text",
                                "text": f"{sign_amount}{change_amount:.2f}",
                                "align": "end",
                                "color": "#666666",
                                "size": "xs",
                            },
                        ],
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": f"資料更新日：{latest_date}",
                        "color": "#888888",
                        "size": "xxs",
                        "align": "center",
                    },
                    {
                        "type": "text",
                        "text": f"資料代碼：{symbol}",
                        "color": "#aaaaaa",
                        "size": "xxs",
                        "align": "center",
                    },
                ],
            },
        }

        return FlexMessage(
            alt_text=f"{stock_code} {period_name}表現",
            contents=FlexContainer.from_dict(flex_json),
        )

    except Exception as exc:
        logger.exception("整理股價訊息失敗：%s", exc)
        return TextMessage(
            text="整理股價資料時發生錯誤，請稍後再試。"
        )


# ==================================================
# 4. 黃金、比特幣、匯率
# ==================================================

def query_market(symbol, label, unit):
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d")

        if history is None or history.empty:
            return f"暫時無法取得{label}資料。"

        current = float(history["Close"].iloc[-1])

        change_text = ""

        if len(history) >= 2:
            previous = float(history["Close"].iloc[-2])

            if previous != 0:
                change_pct = (current - previous) / previous * 100
                sign = "+" if change_pct > 0 else ""

                change_text = (
                    f"\n📈 較前一筆：{sign}{change_pct:.2f}%"
                )

        return (
            f"📊【{label}】\n"
            f"最新可取得價格：{current:,.4f} {unit}"
            f"{change_text}\n"
            "※ 行情可能延遲。"
        )

    except Exception as exc:
        logger.exception("%s 查詢失敗：%s", label, exc)
        return f"查詢{label}失敗，請稍後再試。"


# ==================================================
# 5. Flask 與 LINE Webhook
# ==================================================

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
        logger.warning("LINE Webhook 簽章驗證失敗")
        abort(400)
    except Exception:
        logger.exception("Webhook 處理失敗")
        abort(500)

    return "OK", 200


# ==================================================
# 6. 處理 LINE 使用者訊息
# ==================================================

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = (event.message.text or "").strip()
    upper_text = user_text.upper()

    try:
        # 功能選單
        if (
            any(word in user_text for word in [
                "選單", "功能", "幫助", "使用說明"
            ])
            or upper_text == "HELP"
        ):
            reply_text(
                event.reply_token,
                "🤖【川投顧｜功能選單】\n"
                "• 股票表現：2330、0050、AAPL\n"
                "• 三大法人籌碼：籌碼 2330\n"
                "• 本益比與 EPS：本益比 2330\n"
                "• 黃金行情：黃金\n"
                "• 比特幣行情：比特幣\n"
                "• 美元匯率：美元\n"
                "• 日圓匯率：日圓",
            )
            return

        # 時間區間按鈕，例如 2330_1mo
        if "_" in user_text:
            parts = user_text.rsplit("_", 1)

            if (
                len(parts) == 2
                and parts[1].lower() in {
                    "2d", "1wk", "1mo", "3mo", "6mo", "ytd"
                }
            ):
                stock_code = parts[0].strip().upper()
                period = parts[1].lower()

                if not stock_code:
                    reply_text(event.reply_token, "請輸入股票代碼。")
                    return

                message = build_stock_period_message(
                    stock_code,
                    period,
                )

                send_messages(event.reply_token, [message])
                return

        # 三大法人
        if user_text.startswith(("籌碼", "外資", "法人")):
            stock_code = user_text

            for word in ("籌碼", "外資", "法人"):
                stock_code = stock_code.replace(word, "")

            stock_code = stock_code.strip().upper() or "2330"

            reply_text(
                event.reply_token,
                query_chips_and_institutional(stock_code),
            )
            return

        # 本益比
        if user_text.startswith("本益比"):
            stock_code = (
                user_text.replace("本益比", "", 1)
                .strip()
                .upper()
                or "2330"
            )

            reply_text(
                event.reply_token,
                query_valuation(stock_code),
            )
            return

        # 黃金
        if upper_text in {"黃金", "金價", "GOLD"}:
            reply_text(
                event.reply_token,
                query_market(
                    "GC=F",
                    "國際黃金",
                    "美元/盎司",
                ),
            )
            return

        # 比特幣
        if upper_text in {"比特幣", "BTC"}:
            reply_text(
                event.reply_token,
                query_market(
                    "BTC-USD",
                    "比特幣",
                    "美元",
                ),
            )
            return

        # 美元匯率
        if upper_text in {"美元", "美金", "USD", "匯率"}:
            reply_text(
                event.reply_token,
                query_market(
                    "USDTWD=X",
                    "美元匯率",
                    "新台幣/美元",
                ),
            )
            return

        # 日圓匯率
        if upper_text in {"日圓", "日幣", "JPY"}:
            reply_text(
                event.reply_token,
                query_market(
                    "JPYTWD=X",
                    "日圓匯率",
                    "新台幣/日圓",
                ),
            )
            return

        # 單獨輸入股票代碼：顯示時間區間選單
        if len(user_text) <= 10 and (
            user_text.isdigit()
            or (
                user_text.isascii()
                and user_text.replace(".", "").isalnum()
            )
        ):
            stock_code = upper_text

            template = ButtonsTemplate(
                title=f"查詢 {stock_code} 表現",
                text="請選擇想查看的時間區間：",
                actions=[
                    MessageAction(
                        label="一週表現",
                        text=f"{stock_code}_1wk",
                    ),
                    MessageAction(
                        label="一個月表現",
                        text=f"{stock_code}_1mo",
                    ),
                    MessageAction(
                        label="三個月表現",
                        text=f"{stock_code}_3mo",
                    ),
                    MessageAction(
                        label="年初至今",
                        text=f"{stock_code}_ytd",
                    ),
                ],
            )

            send_messages(
                event.reply_token,
                [
                    TemplateMessage(
                        alt_text=f"請選擇 {stock_code} 的查詢時間區間",
                        template=template,
                    )
                ],
            )
            return

        reply_text(
            event.reply_token,
            "🤖 找不到此指令！請輸入「選單」查看功能。",
        )

    except Exception:
        logger.exception("處理使用者訊息失敗")


# ==================================================
# 啟動
# ==================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
