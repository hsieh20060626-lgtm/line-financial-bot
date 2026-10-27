from datetime import datetime, timedelta
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
import requests
import yfinance as yf

app = Flask(__name__)

# ================= LINE 金鑰設定 =================
configuration = Configuration(
    access_token="MlTIOPWt1TnV2+o+Bh/3xAnuhoexn9EuaoAuJzNiF6uGm86dfvCeJxU55x9hWsGmRpbPINgyTGd8u8l1KLjNiEr3JCCl9qCDTgO/xJsndyQiiQMPoxV3swHMgPKDxipsNTbC/GEUePUZtOdNd95XvQdB04t89/1O/w1cDnyilFU="
)
handler = WebhookHandler("885ff8898009eb79ba69565a4d2658a9")


# ================= 核心查詢邏輯 =================
def query_stock_price(message):
    stock_code = message.strip().upper()
    if not stock_code:
        return "請輸入股票代碼。"

    ticker_symbol = f"{stock_code}.TW" if stock_code.isdigit() else stock_code

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="5d")

        if df.empty or len(df) < 2:
            return f"找不到代碼為「{stock_code}」的股價資料，請確認輸入是否正確！"

        company_name = ticker.info.get("shortName", stock_code)
        latest_data = df.iloc[-1]
        prev_data = df.iloc[-2]

        latest_date = df.index[-1].strftime("%Y年%m月%d日")
        open_price = round(latest_data["Open"], 2)
        low_price = round(latest_data["Low"], 2)
        close_price = round(latest_data["Close"], 2)
        volume = int(latest_data["Volume"])

        prev_close = prev_data["Close"]
        change_pct = ((close_price - prev_close) / prev_close) * 100
        sign = "+" if change_pct > 0 else ""

        return (
            f"目前最新的【{company_name}】股價資料是 {latest_date}\n"
            f"開盤價是 {open_price} 元、最低價是 {low_price} 元\n"
            f"收盤價是 {close_price} 元，本日交易量是 {volume:,} 股\n"
            f"今日漲跌幅是 {sign}{change_pct:.2f}%"
        )
    except Exception:
        return f"系統處理「{stock_code}」時發生異常，請稍後再試。"


# ================= 接收 LINE 訊息的路由 =================
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ================= 處理使用者傳來的文字 =================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        客戶輸入的文字 = event.message.text.strip()

        # 功能選單
        if 客戶輸入的文字 in ["選單", "功能", "幫助", "Help", "help", "使用說明"]:
            ans = (
                "🤖【AI 金融行銷小助理 - 功能選單】\n"
                "• 查詢即時股價：直接輸入代碼（例：2330、0050）\n"
                "• 查詢黃金行情：輸入「黃金」\n"
                "• 查詢比特幣：輸入「比特幣」\n"
                "• 查詢匯率：輸入「美元」或「日圓」"
            )
        # 查黃金
        elif 客戶輸入的文字 in ["黃金", "金價", "GOLD", "gold"]:
            try:
                gold = yf.Ticker("GC=F")
                df = gold.history(period="5d")
                if not df.empty and len(df) >= 2:
                    cp = round(df.iloc[-1]["Close"], 2)
                    pc = df.iloc[-2]["Close"]
                    cpct = ((cp - pc) / pc) * 100
                    sign = "+" if cpct > 0 else ""
                    ans = f"🥇【國際黃金】最新價格：${cp:,} 美元/盎司 (漲跌幅: {sign}{cpct:.2f}%)"
                else:
                    ans = "暫時無法取得黃金數據。"
            except Exception:
                ans = "查詢黃金發生異常。"
        # 查比特幣
        elif 客戶輸入的文字 in ["比特幣", "BTC", "btc"]:
            try:
                btc = yf.Ticker("BTC-USD")
                df = btc.history(period="5d")
                if not df.empty and len(df) >= 2:
                    cp = round(df.iloc[-1]["Close"], 2)
                    pc = df.iloc[-2]["Close"]
                    cpct = ((cp - pc) / pc) * 100
                    sign = "+" if cpct > 0 else ""
                    ans = f"🪙【比特幣】最新價格：${cp:,} 美元 (漲跌幅: {sign}{cpct:.2f}%)"
                else:
                    ans = "暫時無法取得比特幣數據。"
            except Exception:
                ans = "查詢比特幣發生異常。"
        # 查匯率
        elif 客戶輸入的文字 in ["美元", "美金", "USD", "日圓", "日幣", "JPY", "匯率"]:
            try:
                curr_code = "JPYTWD=X" if "日" in 客戶輸入的文字 or "JPY" in 客戶輸入的文字.upper() else "USDTWD=X"
                fx = yf.Ticker(curr_code)
                df = fx.history(period="5d")
                if not df.empty and len(df) >= 2:
                    cp = round(df.iloc[-1]["Close"], 4)
                    ans = f"💱 最新匯率：{cp} 新台幣 (TWD)"
                else:
                    ans = "暫時無法取得匯率數據。"
            except Exception:
                ans = "查詢匯率發生異常。"
        # 一般查股價
        else:
            ans = query_stock_price(客戶輸入的文字)

        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=ans)]
            )
        )


# ================= 啟動伺服器 =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
