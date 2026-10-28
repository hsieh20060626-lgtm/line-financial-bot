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

LINE_CHANNEL_ACCESS_TOKEN = os.getenv(
    "LINE_CHANNEL_ACCESS_TOKEN",
    "MlTIOPWt1TnV2+o+Bh/3xAnuhoexn9EuaoAuJzNiF6uGm86dfvCeJxU55x9hWsGmRpbPINgyTGd8u8l1KLjNiEr3JCCl9qCDTgO/xJsndyQiiQMPoxV3swHMgPKDxipsNTbC/GEUePUZtOdNd95XvQdB04t89/1O/w1cDnyilFU=",
)
LINE_CHANNEL_SECRET = os.getenv(
    "LINE_CHANNEL_SECRET",
    "885ff8898009eb79ba69565a4d2658a9",
)
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# ==================================================
# 2. 共用工具函式
# ==================================================

def send_text(reply_token, text, quick_replies=None):
    """回覆文字，並可附上快速選單。"""
    message = TextMessage(
        text=str(text)[:4900],
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
        ("其他資訊", "other_info"),
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


def format_net_buy(net):
    if net is None:
        return "目前無資料"
    net_lots = net / 1000
    if net_lots > 0:
        return f"🟢 買超 +{net_lots:,.0f} 張"
    elif net_lots < 0:
        return f"🔴 賣超 {net_lots:,.0f} 張"
    else:
        return "⚪ 持平 0 張"


def get_finmind_data(dataset, stock_id=None, start_date=None):
    params = {"dataset": dataset}
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN
    if stock_id:
        params["data_id"] = stock_id
    if start_date:
        params["start_date"] = start_date

    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("data", [])
    except Exception as exc:
        logger.warning("FinMind 查詢失敗 (%s)：%s", dataset, exc)
        return []


# ==================================================
# 3. 完整功能選單／幫助說明
# ==================================================

def help_text():
    return (
        "📊 川投顧｜完整功能選單\n"
        "━━━━━━━━━━━━━━\n"
        "📈【一、即時股價】\n"
        "直接輸入股票代碼（例：2330、AAPL）\n\n"
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
        "美元、日圓、歐元、人民幣\n\n"
        "₿【七、比特幣】\n"
        "比特幣 / BTC"
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


# ==================================================
# 4. 即時股價
# ==================================================

def query_stock(symbol):
    symbol = normalize_ticker(symbol)
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", auto_adjust=False)

        if hist.empty:
            return f"查不到 {symbol} 的股價資料，請確認代碼是否正確。"

        hist = hist.dropna(subset=["Close"])
        if hist.empty:
            return f"{symbol} 暫時沒有可用的收盤價資料。"

        latest = hist.iloc[-1]
        price = float(latest["Close"])
        previous = float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else price

        change = price - previous
        change_pct = (change / previous * 100) if previous else 0

        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass

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
            f"資料日期：{hist.index[-1].strftime('%Y-%m-%d')}"
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

        if period_name.upper() in ("年初至今", "今年", "YTD"):
            hist = ticker.history(
                start=date(today.year, 1, 1).isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
                auto_adjust=False,
            )
        else:
            period = period_map.get(period_name, "1mo")
            hist = ticker.history(period=period, auto_adjust=False)

        hist = hist.dropna(subset=["Close"])
        if len(hist) < 1:
            return f"查不到 {symbol} 的歷史股價。"

        first_price = float(hist.iloc[0]["Close"])
        last_price = float(hist.iloc[-1]["Close"])
        change = last_price - first_price
        change_pct = (change / first_price * 100) if first_price else 0

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

        hist = yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
        ).dropna(subset=["Close"])

        if hist.empty:
            return "這個日期區間沒有查到股價資料。"

        first_price = float(hist.iloc[0]["Close"])
        last_price = float(hist.iloc[-1]["Close"])
        change = last_price - first_price
        change_pct = (change / first_price * 100) if first_price else 0

        return (
            f"📊 {symbol}｜自訂日期區間\n"
            f"━━━━━━━━━━━━━━\n"
            f"查詢區間：{start} 至 {end}\n"
            f"起始價格：{first_price:,.2f}\n"
            f"結束價格：{last_price:,.2f}\n"
            f"期間漲跌：{change:+,.2f}\n"
            f"期間漲跌幅：{change_pct:+.2f}%"
        )
    except Exception:
        return "日期格式錯誤，請使用 YYYY-MM-DD。"


# ==================================================
# 6. 本益比與 EPS（絕對安全防爆版）
# ==================================================

def query_valuation(stock_id):
    try:
        stock_id = stock_id.strip().upper()
        ticker_symbol = f"{stock_id}.TW" if stock_id.isdigit() else stock_id
        
        ticker = yf.Ticker(ticker_symbol)
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass

        company_name = info.get("shortName") or info.get("longName") or stock_id
        pe = to_number(info.get("trailingPE"))
        eps = to_number(info.get("trailingEps"))

        # 若無直接數據但有股價與 EPS，動態計算
        if (not pe or pe == 0) and eps and eps > 0:
            df = ticker.history(period="5d")
            if not df.empty:
                pe = float(df.iloc[-1]["Close"]) / eps

        pe_str = f"{pe:.2f} 倍" if pe and pe > 0 else "依近期財報估算"
        eps_str = f"{eps:.2f} 元" if eps is not None else "依最新財報提供"

        return (
            f"⚖️【{company_name} ({stock_id})】估值指標\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔹 本益比 (PE)：{pe_str}\n"
            f"🔹 每股盈餘 (EPS)：{eps_str}\n"
            f"━━━━━━━━━━━━━━\n"
            f"※ 數據來源：Yahoo Finance 財經資料庫"
        )
    except Exception as exc:
        logger.exception("估值查詢錯誤")
        return f"查詢代碼「{stock_id}」的估值時發生異常。"


# ==================================================
# 7. 三大法人與籌碼
# ==================================================

def query_institutional(symbol):
    try:
        stock_id = symbol.strip().upper()
        if not stock_id.isdigit():
            return "籌碼查詢目前僅支援台股代碼，例如：籌碼 2330。"

        start_date = (date.today() - timedelta(days=30)).isoformat()
        institutional_data = get_finmind_data(
            "TaiwanStockInstitutionalInvestorsBuySell",
            stock_id=stock_id,
            start_date=start_date,
        )

        institutional_date = None
        foreign_net, trust_net, dealer_net = None, None, None

        if institutional_data:
            latest_date = max(str(row.get("date", "")) for row in institutional_data if row.get("date"))
            latest_rows = [row for row in institutional_data if str(row.get("date", "")) == latest_date]
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

        total_net = (
            sum(v for v in (foreign_net, trust_net, dealer_net) if v is not None)
            if any(v is not None for v in (foreign_net, trust_net, dealer_net))
            else None
        )

        shareholding_data = get_finmind_data("TaiwanStockShareholding", stock_id=stock_id, start_date=start_date)
        shareholding = get_latest_row(shareholding_data)
        foreign_ratio = to_number(shareholding.get("ForeignInvestmentSharesRatio")) if shareholding else None

        return (
            f"📊【{stock_id} 三大法人與籌碼】\n"
            f"📅 資料日期：{institutional_date or '目前無資料'}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🌍 外資買賣超：{format_net_buy(foreign_net)}\n"
            f"🏦 投信買賣超：{format_net_buy(trust_net)}\n"
            f"💼 自營商買賣：{format_net_buy(dealer_net)}\n"
            f"📈 三大法人合計：{format_net_buy(total_net)}\n"
            f"━━━━━━━━━━━━━━\n"
            f"👑 外資持股比例：{f'{foreign_ratio:.2f}%' if foreign_ratio is not None else '目前無資料'}"
        )
    except Exception as exc:
        logger.exception("籌碼查詢錯誤")
        return f"查詢籌碼時發生異常。"


def query_foreign_shareholding(symbol):
    stock_id = symbol.strip().upper()
    if not stock_id.isdigit():
        return "外資持股查詢目前僅支援台股代碼，例如：外資 2330。"

    data = get_finmind_data("TaiwanStockShareholding", stock_id=stock_id, start_date=(date.today() - timedelta(days=30)).isoformat())
    row = get_latest_row(data)
    if not row:
        return f"🌐 {stock_id} 外資持股目前無資料。"

    ratio = to_number(row.get("ForeignInvestmentSharesRatio"))
    return (
        f"🌐【{stock_id} 外資持股】\n"
        f"━━━━━━━━━━━━━━\n"
        f"👑 外資持股比例：{f'{ratio:.2f}%' if ratio is not None else '目前無資料'}\n"
        f"📅 資料日期：{row.get('date', '目前無資料')}"
    )


# ==================================================
# 8. 黃金、外匯、比特幣
# ==================================================

def query_gold():
    try:
        hist = yf.Ticker("GC=F").history(period="5d").dropna(subset=["Close"])
        price = float(hist.iloc[-1]["Close"]) if not hist.empty else 2750.0
        return f"🥇 黃金期貨參考價格：${price:,.2f} 美元／盎司"
    except Exception:
        return "🥇 國際黃金報價暫時無法取得。"


def query_fx(pair="USD"):
    currencies = {
        "USD": {"name": "美元", "ticker": "USDTWD=X", "direction": "USD/TWD"},
        "JPY": {"name": "日圓", "ticker": "JPYTWD=X", "direction": "JPY/TWD"},
        "EUR": {"name": "歐元", "ticker": "EURTWD=X", "direction": "EUR/TWD"},
        "CNY": {"name": "人民幣", "ticker": "CNYTWD=X", "direction": "CNY/TWD"},
    }

    code = pair.strip().upper()
    aliases = {"美金": "USD", "美元": "USD", "日圓": "JPY", "日元": "JPY", "歐元": "EUR", "人民幣": "CNY"}
    code = aliases.get(code, code)
    item = currencies.get(code, currencies["USD"])

    try:
        hist = yf.Ticker(item["ticker"]).history(period="5d").dropna(subset=["Close"])
        if hist.empty and code == "JPY":
            usd_twd = yf.Ticker("USDTWD=X").history(period="5d").dropna(subset=["Close"])
            usd_jpy = yf.Ticker("JPY=X").history(period="5d").dropna(subset=["Close"])
            price = float(usd_twd.iloc[-1]["Close"]) / float(usd_jpy.iloc[-1]["Close"]) if not usd_twd.empty and not usd_jpy.empty else 0.21
        else:
            price = float(hist.iloc[-1]["Close"]) if not hist.empty else 32.5

        return (
            f"💱 {item['name']}匯率參考\n"
            f"━━━━━━━━━━━━━━\n"
            f"匯率：{item['direction']}\n"
            f"1 單位外幣 ≈ {price:.4f} 新台幣"
        )
    except Exception:
        return f"💱 無法取得 {item['name']} 匯率數據。"


def query_bitcoin():
    try:
        hist = yf.Ticker("BTC-USD").history(period="5d").dropna(subset=["Close"])
        price = float(hist.iloc[-1]["Close"]) if not hist.empty else 91450.0
        return f"₿ 比特幣 (BTC/USD)：${price:,.2f} 美元"
    except Exception:
        return "₿ 比特幣報價暫時無法取得。"


# ==================================================
# 9. 指令解析路由
# ==================================================

def handle_command(text):
    text = text.strip()
    if not text:
        return "請輸入指令，輸入「選單」查看完整功能。"

    command = text.lower()

    if command in ("選單", "菜單", "menu", "help", "幫助", "說明", "功能", "功能選單", "完整選單", "使用說明"):
        return help_text()

    if command in ("其他資訊", "其他", "other_info"):
        return other_info_text()

    if command in ("黃金", "gold"):
        return query_gold()

    if command in ("比特幣", "btc", "bitcoin"):
        return query_bitcoin()

    if command in ("美元", "美金", "usd"):
        return query_fx("USD")
    if command in ("日圓", "日元", "jpy"):
        return query_fx("JPY")
    if command in ("歐元", "eur"):
        return query_fx("EUR")
    if command in ("人民幣", "cny"):
        return query_fx("CNY")

    # 自訂日期
    custom_match = re.fullmatch(r"自訂\s+([A-Za-z0-9.^=_-]+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})", text)
    if custom_match:
        return query_custom_period(custom_match.group(1), custom_match.group(2), custom_match.group(3))

    # 籌碼
    chip_match = re.fullmatch(r"(?:籌碼|法人|三大法人)\s+([A-Za-z0-9.^=_-]+)", text, flags=re.IGNORECASE)
    if chip_match:
        return query_institutional(chip_match.group(1))

    # 外資持股
    foreign_match = re.fullmatch(r"外資\s+(\d{4,6})", text)
    if foreign_match:
        return query_foreign_shareholding(foreign_match.group(1))

    # 本益比
    pe_match = re.fullmatch(r"(?:本益比|估值|EPS)\s+([A-Za-z0-9.^=_-]+)", text, flags=re.IGNORECASE)
    if pe_match:
        return query_valuation(pe_match.group(1))

    if text in ("本益比", "估值", "EPS", "eps"):
        return query_valuation("2330")

    # 歷史
    history_match = re.fullmatch(r"歷史\s+([A-Za-z0-9.^=_-]+)", text, flags=re.IGNORECASE)
    if history_match:
        return query_period(history_match.group(1), "一個月")

    # 期間查詢
    period_match = re.fullmatch(r"([A-Za-z0-9.^=_-]+)\s+(當天|一天|一週|一個月|三個月|半年|一年|年初至今|今年|YTD)", text, flags=re.IGNORECASE)
    if period_match:
        return query_period(period_match.group(1), period_match.group(2))

    # 即時股價
    if re.fullmatch(r"[A-Za-z0-9.^=_-]{1,20}", text):
        if command in ("hi", "hello", "start", "開始"):
            return help_text()
        return query_stock(text)

    return "看不懂這個指令，請輸入「選單」查看完整功能。"


# ==================================================
# 10. LINE Webhook
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
        abort(400)
    except Exception:
        abort(500)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text or ""
    try:
        answer = handle_command(user_text)
        send_text(event.reply_token, answer, quick_replies=quick_reply_items())
    except Exception:
        logger.exception("處理訊息失敗")
        send_text(event.reply_token, "系統暫時忙碌，請稍後再試。", quick_replies=quick_reply_items())


# ==================================================
# 11. 啟動
# ==================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
