import os
import re
import time
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


def require_env(name):
    """金鑰只從環境變數讀取，沒設定就直接報錯，避免誤用或外洩。"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少環境變數：{name}")
    return value


LINE_CHANNEL_ACCESS_TOKEN = require_env("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = require_env("LINE_CHANNEL_SECRET")
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DISCLAIMER = "※ 資料僅供參考，非投資建議。"


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


_resolved = {}


def normalize_ticker(symbol):
    """
    將純數字台股代碼轉為 Yahoo Finance 格式。
    上市用 .TW、上櫃用 .TWO：先試 .TW，沒資料再試 .TWO。
    """
    symbol = symbol.strip().upper()
    if not symbol.isdigit():
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
        "比特幣 / BTC\n\n"
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


# ==================================================
# 4. 即時股價
# ==================================================

def query_stock(symbol):
    symbol = normalize_ticker(symbol)
    try:
        hist = fetch_history(symbol, period="5d")

        if hist.empty:
            return f"查不到 {symbol} 的股價資料，請確認代碼是否正確。"

        latest = hist.iloc[-1]
        price = float(latest["Close"])
        previous = float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else price

        change = price - previous
        change_pct = (change / previous * 100) if previous else 0

        info = get_info(symbol)
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
        today = date.today()

        if period_name.upper() in ("年初至今", "今年", "YTD"):
            hist = fetch_history(
                symbol,
                start=date(today.year, 1, 1).isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
            )
        else:
            period = period_map.get(period_name, "1mo")
            hist = fetch_history(symbol, period=period)

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
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return "日期格式錯誤，請使用 YYYY-MM-DD。"

    if start >= end:
        return "日期格式錯誤：開始日期必須早於結束日期。"

    symbol = normalize_ticker(symbol)
    try:
        hist = fetch_history(
            symbol,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
        )

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
    except Exception as exc:
        logger.exception("自訂區間查詢錯誤")
        return f"歷史股價查詢失敗：{type(exc).__name__}"


# ==================================================
# 6. 本益比與 EPS：最新股價 ÷ 最近四季 EPS
# ==================================================

def query_valuation(stock_id):
    stock_id = stock_id.strip().upper()

    try:
        # 台股自動判斷 .TW / .TWO；美股直接使用股票代號
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

        # 2. 取得公司名稱
        info = get_info(ticker_symbol)

        company_name = (
            info.get("shortName")
            or info.get("longName")
            or stock_id
        )

        currency = info.get("currency", "")
        currency_text = f" {currency}" if currency else ""

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
            return (
                f"⚠️【{company_name}（{stock_id}）估值】\n"
                "━━━━━━━━━━━━━━\n"
                f"最新可用股價：{latest_price:,.2f}"
                f"{currency_text}\n"
                f"股價資料日期：{price_date}\n"
                "最近四季 EPS：資料不足，無法可靠計算\n"
                "本益比：目前無法計算\n\n"
                "原因：Yahoo Finance 未提供足夠的季度 EPS。\n"
                "請檢查該股票的季度財報資料是否完整。"
            )

        # 5. 加總最近四季 EPS
        eps_ttm = sum(eps_values)

        # EPS 必須大於 0，才顯示一般正本益比
        if eps_ttm > 0:
            pe = latest_price / eps_ttm
            pe_text = f"{pe:.2f} 倍"
        elif eps_ttm < 0:
            pe_text = "不適用（最近四季 EPS 為負）"
        else:
            pe_text = "不適用（最近四季 EPS 為零）"

        eps_detail = "\n".join(
            f"第{i + 1}季 EPS：{value:.2f}"
            for i, value in enumerate(eps_values)
        )

        return (
            f"⚖️【{company_name}（{stock_id}）估值】\n"
            "━━━━━━━━━━━━━━\n"
            f"💵 最新可用股價：{latest_price:,.2f}"
            f"{currency_text}\n"
            f"📅 股價資料日期：{price_date}\n\n"
            f"🧾 最近四季 EPS 合計：{eps_ttm:.2f}\n"
            f"{eps_detail}\n"
            "━━━━━━━━━━━━━━\n"
            f"📈 本益比（PE）：{pe_text}\n"
            "計算方式：最新可用股價 ÷ 最近四季 EPS 合計\n"
            "📌 EPS 與股價的資料日期可能不同，"
            "請以各自公布日期為準。"
        )

    except Exception as exc:
        logger.exception("估值查詢錯誤")
        return (
            f"⚠️ 查詢 {stock_id} 的估值時發生錯誤："
            f"{type(exc).__name__}"
        )


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

        return (
            f"📊【{stock_id} 三大法人與籌碼】\n"
            f"📅 資料日期：{institutional_date or '目前無資料'}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🌍 外資買賣超：{format_net_buy(foreign_net)}\n"
            f"🏦 投信買賣超：{format_net_buy(trust_net)}\n"
            f"💼 自營商買賣：{format_net_buy(dealer_net)}\n"
            f"📈 三大法人合計：{format_net_buy(total_net)}\n"
            f"━━━━━━━━━━━━━━\n"
            f"👑 外資持股比例：{ratio_text}"
        )
    except Exception:
        logger.exception("籌碼查詢錯誤")
        return "查詢籌碼時發生異常。"


def query_foreign_shareholding(symbol):
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
        return f"🌐 {stock_id} 外資持股目前無資料。"

    ratio = to_number(row.get("ForeignInvestmentSharesRatio"))
    ratio_text = f"{ratio:.2f}%" if ratio is not None else "目前無資料"
    return (
        f"🌐【{stock_id} 外資持股】\n"
        f"━━━━━━━━━━━━━━\n"
        f"👑 外資持股比例：{ratio_text}\n"
        f"📅 資料日期：{row.get('date', '目前無資料')}"
    )


# ==================================================
# 8. 黃金、外匯、比特幣（查不到就如實回報，不使用假數字）
# ==================================================

def latest_close(ticker):
    """回傳最新收盤價；查不到回傳 None。"""
    try:
        hist = fetch_history(ticker, period="5d")
        if hist.empty:
            return None
        return float(hist.iloc[-1]["Close"])
    except Exception as exc:
        logger.warning("報價查詢失敗 (%s)：%s", ticker, exc)
        return None


def query_gold():
    price = latest_close("GC=F")
    if price is None:
        return "🥇 國際黃金報價暫時無法取得。"
    return f"🥇 黃金期貨參考價格：${price:,.2f} 美元／盎司"


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

    price = latest_close(item["ticker"])

    # 日圓若直接查不到，改用 USD/TWD ÷ USD/JPY 推算
    if price is None and code == "JPY":
        usd_twd = latest_close("USDTWD=X")
        usd_jpy = latest_close("JPY=X")
        if usd_twd and usd_jpy:
            price = usd_twd / usd_jpy

    if price is None:
        return f"💱 無法取得 {item['name']} 匯率數據。"

    return (
        f"💱 {item['name']}匯率參考\n"
        f"━━━━━━━━━━━━━━\n"
        f"匯率：{item['direction']}\n"
        f"1 單位外幣 ≈ {price:.4f} 新台幣"
    )


def query_bitcoin():
    price = latest_close("BTC-USD")
    if price is None:
        return "₿ 比特幣報價暫時無法取得。"
    return f"₿ 比特幣 (BTC/USD)：${price:,.2f} 美元"


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
        answer = handle_command(user_text)
        send_text(event.reply_token, answer, quick_replies=quick_reply_items())
    except Exception:
        logger.exception("處理訊息失敗")
        try:
            send_text(event.reply_token, "系統暫時忙碌，請稍後再試。", quick_replies=quick_reply_items())
        except Exception:
            logger.exception("備用回覆也失敗")


# ==================================================
# 11. 啟動（正式環境請用：gunicorn app:app）
# ==================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
