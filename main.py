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
# 2. 共用工具函式（確保所有模組皆可安全呼叫）
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


def to_number(value):
    """將資料安全轉為浮點數；缺漏或格式不符時回傳 None。"""
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
    """將法人淨買賣超股數轉成張數顯示。"""
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
    """取得 FinMind 真實資料（有 Token 則帶入，無 Token 亦可使用公開額度）。"""
    params = {
        "dataset": dataset,
    }
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
            timeout=12,
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
        "外資 2330\n"
        "查詢外資、投信、自營商買賣超、外資持股與融資融券。\n\n"
        "🧮【四、本益比與 EPS】\n"
        "本益比 2330\n"
        "EPS 2330\n"
        "本益比 AAPL\n\n"
        "🥇【五、黃金】\n"
        "黃金\n\n"
        "💱【六、外匯匯率】\n"
        "美元 / USD\n"
        "日圓 / JPY\n"
        "人民幣 / CNY\n"
        "港幣 / HKD\n"
        "韓元 / KRW\n"
        "歐元 / EUR\n\n"
        "₿【七、比特幣】\n"
        "比特幣 / BTC\n\n"
        "━━━━━━━━━━━━━━\n"
        "⚠️ 本機器人提供金融資訊，不代表投資建議。"
    )


def other_info_text():
    return (
        "🌐 川投顧｜其他金融資訊\n\n"
        "🥇 黃金：輸入「黃金」\n"
        "💱 美元兌台幣：輸入「美元」或「USD」\n"
        "💱 日圓兌台幣：輸入「日圓」或「JPY」\n"
        "💱 人民幣兌台幣：輸入「人民幣」或「CNY」\n"
        "💱 歐元兌台幣：輸入「歐元」或「EUR」\n"
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

        if period_name.upper() in ("年初至今", "今年", "YTD"):
            hist = ticker.history(
                start=date(today.year, 1, 1).isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
                auto_adjust=False,
            )
        else:
            period = period_map.get(period_name)
            if not period:
                return "不支援這個期間。可使用：當天、一週、一個月、三個月、半年、一年、年初至今。"
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
    except ValueError:
        return "日期格式錯誤，請使用 YYYY-MM-DD。"
    except Exception as exc:
        logger.exception("自訂日期查詢錯誤")
        return f"自訂日期查詢失敗：{type(exc).__name__}"


# ==================================================
# 6. 本益比與 EPS（100% 真實數據：台股 FinMind + 美股 yfinance）
# ==================================================

def query_valuation(stock_id):
    try:
        stock_id = stock_id.strip().upper()
        today = date.today()

        # --------------------------------------------------
        # 情況 A：台股查詢（純數字代碼，如 2330、2406）
        # --------------------------------------------------
        if stock_id.isdigit():
            start_date = (today - timedelta(days=60)).isoformat()

            # 1. 抓 FinMind 台股真實每日估值 (PER, PBR, 殖利率)
            per_data = get_finmind_data(
                "TaiwanStockPER",
                stock_id=stock_id,
                start_date=start_date,
            )
            latest_per = get_latest_row(per_data)

            # 2. 抓 FinMind 台股真實季財報 (找 EPS)
            eps_data = get_finmind_data(
                "TaiwanStockFinancialStatements",
                stock_id=stock_id,
                start_date=(today - timedelta(days=400)).isoformat(),
            )
            eps_rows = [
                row for row in eps_data
                if str(row.get("type", "")).upper() == "EPS"
                or "每股盈餘" in str(row.get("origin_name", ""))
            ]
            eps_rows = sorted(eps_rows, key=lambda x: str(x.get("date", "")), reverse=True)
            latest_eps_row = eps_rows[0] if eps_rows else None

            # 同時用 yfinance 抓最新參考股價與公司名稱
            current_price = None
            company_name = stock_id
            try:
                ticker = yf.Ticker(f"{stock_id}.TW")
                hist = ticker.history(period="5d")
                if not hist.empty:
                    current_price = float(hist["Close"].dropna().iloc[-1])
                info = ticker.info or {}
                company_name = info.get("shortName") or info.get("longName") or stock_id
            except Exception:
                pass

            if not latest_per and not latest_eps_row:
                return (
                    f"⚠️【{stock_id} 本益比與 EPS】\n"
                    "目前從台股資料庫查無此代碼的本益比或財報資料（可能為 ETF 或近期無盈餘資料）。"
                )

            lines = [
                f"⚖️【{company_name}（{stock_id}）真實估值指標】",
                "━━━━━━━━━━━━━━",
            ]

            if current_price:
                lines.append(f"💵 最新參考股價：{current_price:,.2f} TWD")

            if latest_per:
                per_val = to_number(latest_per.get("PER"))
                pbr_val = to_number(latest_per.get("PBR"))
                dy_val = to_number(latest_per.get("dividend_yield"))
                per_date = latest_per.get("date", "無資料")

                lines.append(f"📈 本益比 (PER)：{f'{per_val:.2f} 倍' if per_val else '無資料'}")
                lines.append(f"📚 股價淨值比 (PBR)：{f'{pbr_val:.2f}' if pbr_val else '無資料'}")
                lines.append(f"💰 現金殖利率：{f'{dy_val:.2f}%' if dy_val else '無資料'}")
                lines.append(f"📅 估值資料日期：{per_date}")

                if current_price and per_val and per_val > 0:
                    implied_ttm_eps = current_price / per_val
                    lines.append(f"🧮 反推近四季累計 EPS：約 {implied_ttm_eps:.2f} 元")
            else:
                lines.append("📈 本益比 (PER)：目前無資料")

            lines.append("━━━━━━━━━━━━━━")

            if latest_eps_row:
                eps_val = to_number(latest_eps_row.get("value"))
                eps_date = latest_eps_row.get("date", "無資料")
                lines.append(f"🧾 最新單季財報 EPS：{f'{eps_val:.2f} 元' if eps_val is not None else '無資料'}")
                lines.append(f"📅 財報季度日期：{eps_date}")
                if len(eps_rows) >= 4:
                    ttm_eps = sum(to_number(r.get("value")) or 0 for r in eps_rows[:4])
                    lines.append(f"📊 近四季財報合計 EPS：{ttm_eps:.2f} 元")
            else:
                lines.append("🧾 財報 EPS：目前無單季財報明細")

            lines.append("━━━━━━━━━━━━━━")
            lines.append("※ 資料來源：FinMind 台股官方公開資料庫")
            return "\n".join(lines)

        # --------------------------------------------------
        # 情況 B：美股查詢（英文代碼，如 AAPL、NVDA、TSLA）
        # --------------------------------------------------
        else:
            ticker = yf.Ticker(stock_id)
            info = ticker.info or {}
            hist = ticker.history(period="5d")

            current_price = float(hist["Close"].dropna().iloc[-1]) if not hist.empty else to_number(info.get("currentPrice"))
            company_name = info.get("shortName") or info.get("longName") or stock_id

            trailing_pe = to_number(info.get("trailingPE"))
            forward_pe = to_number(info.get("forwardPE"))
            trailing_eps = to_number(info.get("trailingEps"))
            forward_eps = to_number(info.get("forwardEps"))
            pbr = to_number(info.get("priceToBook"))

            if (not trailing_pe) and current_price and trailing_eps and trailing_eps > 0:
                trailing_pe = current_price / trailing_eps

            if not any([trailing_pe, forward_pe, trailing_eps, forward_eps]):
                return f"⚠️ 查無美股【{stock_id}】的估值與 EPS 資料，請確認代碼是否正確。"

            lines = [
                f"⚖️【{company_name}（{stock_id}）美股即時估值】",
                "━━━━━━━━━━━━━━",
            ]
            if current_price:
                lines.append(f"💵 最新股價：${current_price:,.2f} USD")

            lines.append(f"📈 本益比 (TTM PE)：{f'{trailing_pe:.2f} 倍' if trailing_pe else '無資料'}")
            lines.append(f"🔮 預估本益比 (Forward PE)：{f'{forward_pe:.2f} 倍' if forward_pe else '無資料'}")
            lines.append(f"📚 股價淨值比 (PBR)：{f'{pbr:.2f}' if pbr else '無資料'}")
            lines.append("━━━━━━━━━━━━━━")
            lines.append(f"💵 近四季 EPS (TTM)：{f'${trailing_eps:.2f}' if trailing_eps is not None else '無資料'}")
            lines.append(f"🚀 預估未來 EPS (Forward)：{f'${forward_eps:.2f}' if forward_eps is not None else '無資料'}")
            lines.append("━━━━━━━━━━━━━━")
            lines.append("※ 資料來源：Yahoo Finance 即時財報數據")
            return "\n".join(lines)

    except Exception as exc:
        logger.exception("本益比與估值查詢錯誤")
        return f"估值查詢失敗（{stock_id}）：{type(exc).__name__}"


# ==================================================
# 7、8. 完整籌碼報告：三大法人、外資持股、融資融券
# ==================================================

def query_institutional(symbol):
    stock_id = symbol.strip().upper()
    if not stock_id.isdigit():
        return "籌碼查詢目前僅支援台股代碼，例如：籌碼 2330。"

    start_date = (date.today() - timedelta(days=30)).isoformat()

    # A. 三大法人買賣超
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

            if name == "Foreign_Investor":
                f_b += buy
                f_s += sell
                has_f = True
            elif name == "Investment_Trust":
                t_b += buy
                t_s += sell
                has_t = True
            elif name in ("Dealer", "Dealer_self", "Dealer_Hedging"):
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

    # B. 外資持股
    shareholding_data = get_finmind_data(
        "TaiwanStockShareholding",
        stock_id=stock_id,
        start_date=start_date,
    )
    shareholding = get_latest_row(shareholding_data)
    foreign_shares = to_number(shareholding.get("ForeignInvestmentShares")) if shareholding else None
    foreign_ratio = to_number(shareholding.get("ForeignInvestmentSharesRatio")) if shareholding else None
    shareholding_date = shareholding.get("date") if shareholding else None

    # C. 融資融券
    margin_data = get_finmind_data(
        "TaiwanStockMarginPurchaseShortSale",
        stock_id=stock_id,
        start_date=start_date,
    )
    margin = get_latest_row(margin_data)
    margin_balance = to_number(margin.get("MarginPurchaseTodayBalance")) if margin else None
    short_balance = to_number(margin.get("ShortSaleTodayBalance")) if margin else None
    margin_date = margin.get("date") if margin else None

    def show_number(val):
        return f"{val:,.0f}" if val is not None else "目前無資料"

    def show_ratio(val):
        return f"{val:.2f}%" if val is not None else "目前無資料"

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
        f"📅 外資持股日期：{shareholding_date or '目前無資料'}",
        "",
        "📚【融資融券】",
        "------------------------------------",
        f"📈 融資今日餘額：{show_number(margin_balance)}",
        f"📉 融券今日餘額：{show_number(short_balance)}",
        f"📅 融資券日期：{margin_date or '目前無資料'}",
    ]

    return "\n".join(report)


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

    shares = to_number(row.get("ForeignInvestmentShares"))
    ratio = to_number(row.get("ForeignInvestmentSharesRatio"))

    return (
        f"🌐【{stock_id} 外資持股】\n"
        "------------------------------------\n"
        f"👑 外資持股股數：{f'{shares:,.0f} 股' if shares is not None else '目前無資料'}\n"
        f"👑 外資持股比例：{f'{ratio:.2f}%' if ratio is not None else '目前無資料'}\n"
        f"📅 資料日期：{row.get('date', '目前無資料')}"
    )


# ==================================================
# 9. 黃金、外匯、比特幣
# ==================================================

def query_gold():
    try:
        hist = yf.Ticker("GC=F").history(period="5d").dropna(subset=["Close"])
        if hist.empty:
            return "目前無法取得黃金期貨報價。"
        price = float(hist.iloc[-1]["Close"])
        day = hist.index[-1].strftime("%Y-%m-%d")
        return (
            "🥇 黃金期貨參考價格\n"
            f"最新可用價格：{price:,.2f} 美元／金衡盎司\n"
            f"資料日期：{day}\n\n"
            "※ 此為國際黃金期貨報價，不等同台灣銀樓實際金價。"
        )
    except Exception as exc:
        logger.exception("黃金查詢錯誤")
        return f"黃金查詢失敗：{type(exc).__name__}"


def query_fx(pair="USD/TWD"):
    currencies = {
        "USD": {"name": "美元", "ticker": "USDTWD=X", "direction": "USD/
