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
    TemplateMessage,
    ButtonsTemplate,
    MessageAction,
    FlexMessage,
    FlexContainer,
)
import requests
import yfinance as yf

app = Flask(__name__)

# ================= LINE 金鑰設定 =================
configuration = Configuration(
    access_token="MlTIOPWt1TnV2+o+Bh/3xAnuhoexn9EuaoAuJzNiF6uGm86dfvCeJxU55x9hWsGmRpbPINgyTGd8u8l1KLjNiEr3JCCl9qCDTgO/xJsndyQiiQMPoxV3swHMgPKDxipsNTbC/GEUePUZtOdNd95XvQdB04t89/1O/w1cDnyilFU="
)
handler = WebhookHandler("885ff8898009eb79ba69565a4d2658a9")


# ================= 核心功能一：查詢基礎股價 =================
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


# ================= 核心功能二：三大法人買賣超與籌碼 =================
def query_chips_and_institutional(stock_code):
    if not stock_code:
        return "請輸入股票代碼，例如：籌碼 2330 或 籌碼 0050"

    ticker_symbol = f"{stock_code}.TW" if stock_code.isdigit() else stock_code
    company_name = stock_code
    insiders = 0
    institutions = 0

    try:
        info = yf.Ticker(ticker_symbol).info
        company_name = info.get("shortName", stock_code)
        insiders = (info.get("heldPercentInsiders") or 0) * 100
        institutions = (info.get("heldPercentInstitutions") or 0) * 100
    except Exception:
        pass

    if stock_code.isdigit():
        try:
            start_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

            url_inst = "https://api.finmindtrade.com/api/v4/data"
            params_inst = {
                "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                "data_id": stock_code,
                "start_date": start_date,
            }
            r_inst = requests.get(url_inst, params=params_inst, timeout=8).json()
            data_inst = r_inst.get("data", [])

            params_share = {
                "dataset": "TaiwanStockShareholding",
                "data_id": stock_code,
                "start_date": start_date,
            }
            r_share = requests.get(url_inst, params=params_share, timeout=8).json()
            data_share = r_share.get("data", [])

            foreign_hold_pct = institutions
            if data_share:
                latest_share = data_share[-1]
                foreign_hold_pct = float(
                    latest_share.get("ForeignInvestmentSharesRatio", institutions)
                )

            if data_inst:
                latest_date = data_inst[-1]["date"]
                day_records = [d for d in data_inst if d["date"] == latest_date]

                foreign_net_shares = 0
                trust_net_shares = 0
                dealer_net_shares = 0

                for row in day_records:
                    name = row.get("name", "")
                    diff = int(row.get("buy", 0)) - int(row.get("sell", 0))
                    if "Foreign" in name:
                        foreign_net_shares += diff
                    elif "Investment_Trust" in name:
                        trust_net_shares += diff
                    elif "Dealer" in name:
                        dealer_net_shares += diff

                f_lots = round(foreign_net_shares / 1000)
                t_lots = round(trust_net_shares / 1000)
                d_lots = round(dealer_net_shares / 1000)
                total_lots = f_lots + t_lots + d_lots

                def fmt_vol(val):
                    if val > 0:
                        return f"🔴 買超 +{val:,} 張"
                    elif val < 0:
                        return f"🟢 賣超 {val:,} 張"
                    else:
                        return "⚪ 持平 0 張"

                other_pct = max(0, 100 - foreign_hold_pct - insiders)

                return (
                    f"📊【{company_name} ({stock_code})】法人買賣超與籌碼\n"
                    f"📅 資料日期：{latest_date}\n"
                    f"------------------------------------\n"
                    f"🌍 外資買賣超：{fmt_vol(f_lots)}\n"
                    f"🏦 投信買賣超：{fmt_vol(t_lots)}\n"
                    f"💼 自營商買賣：{fmt_vol(d_lots)}\n"
                    f"📈 三大法人合計：{fmt_vol(total_lots)}\n"
                    f"------------------------------------\n"
                    f"👑 外資持股比例：{foreign_hold_pct:.2f}%\n"
                    f"👔 內部大股東持股：{insiders:.2f}%\n"
                    f"👥 本土與一般投資人：{other_pct:.2f}%"
                )
        except Exception:
            pass

    retail = max(0, 100 - insiders - institutions)
    return (
        f"📊【{company_name} ({stock_code})】籌碼持倉成分\n"
        f"------------------------------------\n"
        f"🏦 外資與機構法人：{institutions:.2f}%\n"
        f"👔 公司內部大股東：{insiders:.2f}%\n"
        f"👥 一般散戶與其他：{retail:.2f}%\n"
        f"------------------------------------"
    )


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


# ================= 處理使用者傳來的文字與互動按鈕 =================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        客戶輸入的文字 = event.message.text.strip()

        # 1. 完整功能選單 / 幫助導覽
        if 客戶輸入的文字.startswith(
            ("選單", "功能", "幫助", "Help", "help", "使用說明", "完整功能選單")
        ) or 客戶輸入的文字 in [
            "選單",
            "功能",
            "幫助",
            "Help",
            "help",
            "使用說明",
            "完整功能選單",
        ]:
            ans = (
                "🤖【AI 金融行銷小助理 - 功能選單】\n"
                "歡迎使用數位理財服務，請直接點擊下方圖文選單或傳送以下指令：\n\n"
                "📈 1. 查詢即時股價與互動時間區間\n"
                "   • 直接輸入代碼（例：2330、0050）➔ 自動跳出時間選擇按鈕\n\n"
                "📊 2. 三大法人買賣超與籌碼\n"
                "   • 輸入「籌碼 2330」\n\n"
                "⚖️ 3. 股票估值指標\n"
                "   • 輸入「本益比 2330」\n\n"
                "🪙 4. 黃金與加密貨幣即時行情\n"
                "   • 輸入「黃金」或「比特幣」\n\n"
                "💱 5. 國際外匯即時匯率\n"
                "   • 輸入「美元」或「日圓」"
            )
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=ans)]
                )
            )

        # 2. 處理使用者點擊「時間區間按鈕」後的查詢，回傳精美 Flex 圖卡
        elif "_" in 客戶輸入的文字 and any(p in 客戶輸入的文字 for p in ["_2d", "_1wk", "_1mo", "_3mo", "_6mo", "_ytd"]):
            try:
                parts = 客戶輸入的文字.split("_")
                stock_code = parts[0].upper()
                yf_period = parts[1]

                period_name_map = {
                    "2d": "當日即時",
                    "1wk": "一週 (5日)",
                    "1mo": "一個月",
                    "3mo": "三個月",
                    "6mo": "六個月",
                    "ytd": "年初至今 (YTD)"
                }
                period_name = period_name_map.get(yf_period, "一個月")

                ticker_symbol = f"{stock_code}.TW" if stock_code.isdigit() else stock_code
                ticker = yf.Ticker(ticker_symbol)
                df = ticker.history(period=yf_period)

                if df.empty or len(df) < 2:
                    reply_msg = TextMessage(text=f"找不到代碼為「{stock_code}」在此區間的資料。")
                else:
                    company_name = ticker.info.get("shortName", stock_code)
                    start_price = df.iloc[0]["Close"]
                    end_price = df.iloc[-1]["Close"]
                    latest_date = df.index[-1].strftime("%Y年%m月%d日")
                    
                    change_amt = end_price - start_price
                    change_pct = (change_amt / start_price) * 100
                    sign = "+" if change_pct > 0 else ""
                    
                    is_up = change_pct >= 0
                    header_color = "#e02b2b" if is_up else "#00b900"
                    arrow_emoji = "📈" if is_up else "📉"

                    flex_json = {
                      "type": "bubble",
                      "header": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                          {
                            "type": "text",
                            "text": f"{arrow_emoji} {company_name} ({stock_code})",
                            "color": "#ffffff",
                            "weight": "bold",
                            "size": "lg"
                          },
                          {
                            "type": "text",
                            "text": f"查詢區間：{period_name}",
                            "color": "#ffffffcc",
                            "size": "sm",
                            "margin": "xs"
                          }
                        ],
                        "backgroundColor": header_color,
                        "paddingAll": "lg"
                      },
                      "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                          {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                              {"type": "text", "text": "起始參考價", "color": "#aaaaaa", "size": "sm"},
                              {"type": "text", "text": f"{start_price:.2f} 元", "align": "end", "size": "sm", "weight": "bold"}
                            ]
                          },
                          {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                              {"type": "text", "text": "最新收盤價", "color": "#aaaaaa", "size": "sm"},
                              {"type": "text", "text": f"{end_price:.2f} 元", "align": "end", "size": "sm", "weight": "bold"}
                            ],
                            "margin": "md"
                          },
                          {
                            "type": "separator",
                            "margin": "lg"
                          },
                          {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                              {"type": "text", "text": "區間漲跌幅", "color": "#333333", "size": "md", "weight": "bold"},
                              {"type": "text", "text": f"{sign}{change_pct:.2f}%", "align": "end", "size": "md", "weight": "bold", "color": header_color}
                            ],
                            "margin": "lg"
                          },
                          {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                              {"type": "text", "text": "漲跌金額", "color": "#666666", "size": "xs"},
                              {"type": "text", "text": f"{sign}{change_amt:.2f} 元", "align": "end", "size": "xs", "color": "#666666"}
                            ],
                            "margin": "sm"
                          }
                        ]
                      },
                      "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                          {
                            "type": "text",
                            "text": f"📅 資料更新日：{latest_date}",
                            "color": "#aaaaaa",
                            "size": "xxs",
                            "align": "center"
                          }
                        ],
                        "paddingAll": "md"
                      }
                    }

                    reply_msg = FlexMessage(
                        alt_text=f"【{stock_code}】{period_name}表現報告",
                        contents=FlexContainer.from_dict(flex_json)
                    )
            except Exception:
                reply_msg = TextMessage(text="查詢發生異常，請確認股票代碼是否正確。")

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[reply_msg]
                )
            )

        # 3. 如果使用者直接輸入「股票代碼」，主動跳出時間區間按鈕選單
        elif 客戶輸入_代碼 := 客戶輸入的文字.upper().replace(".TW", ""):
            if 客戶輸入的文字.startswith(("籌碼", "外資", "法人")):
                stock_code = 客戶輸入的文字.replace("籌碼", "").replace("外資", "").replace("法人", "").strip().upper()
                if not stock_code:
                    stock_code = "2330"
                ans = query_chips_and_institutional(stock_code)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ans)])
                )
            elif 客戶輸入的文字.startswith("本益比"):
                stock_code = 客戶輸入的文字.replace("本益比", "").strip().upper()
                if not stock_code:
                    stock_code = "2330"
                ticker_symbol = f"{stock_code}.TW" if stock_code.isdigit() else stock_code
                try:
                    info = yf.Ticker(ticker_symbol).info
                    company_name = info.get("shortName", stock_code)
                    pe = info.get("trailingPE")
                    eps = info.get("trailingEps")
                    pe_str = f"{pe:.2f} 倍" if pe else "無資料"
                    eps_str = f"{eps:.2f} 元" if eps else "無資料"
                    ans = f"⚖️【{company_name} ({stock_code})】估值指標\n------------------------------------\n🔹 本益比 (PE)：{pe_str}\n🔹 每股盈餘 (EPS)：{eps_str}\n------------------------------------"
                except Exception:
                    ans = f"找不到「{stock_code}」的本益比資料。"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ans)])
                )
            elif 客戶輸入的文字 in ["黃金", "金價", "GOLD", "Gold", "gold"]:
                try:
                    gold = yf.Ticker("GC=F")
                    df = gold.history(period="5d")
                    if not df.empty and len(df) >= 2:
                        cp = round(df.iloc[-1]["Close"], 2)
                        pc = df.iloc[-2]["Close"]
                        cpct = ((cp - pc) / pc) * 100
                        sign = "+" if cpct > 0 else ""
                        ans = f"🥇【國際黃金 (GC=F)】最新市場行情\n------------------------------------\n🔹 黃金期貨價格：${cp:,} 美元/盎司\n🔹 今日漲跌幅：{sign}{cpct:.2f}%\n------------------------------------"
                    else:
                        ans = "暫時無法取得黃金數據。"
                except Exception:
                    ans = "查詢黃金時發生異常。"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ans)])
                )
            elif 客戶輸入的文字 in ["比特幣", "BTC", "btc", "BITCOIN", "Bitcoin"]:
                try:
                    btc = yf.Ticker("BTC-USD")
                    df = btc.history(period="5d")
                    if not df.empty and len(df) >= 2:
                        cp = round(df.iloc[-1]["Close"], 2)
                        pc = df.iloc[-2]["Close"]
                        cpct = ((cp - pc) / pc) * 100
                        sign = "+" if cpct > 0 else ""
                        ans = f"🪙【比特幣 (BTC/USD)】最新市場數據\n------------------------------------\n🔹 最新價格：${cp:,} 美元\n🔹 今日漲跌幅：{sign}{cpct:.2f}%\n------------------------------------"
                    else:
                        ans = "暫時無法取得比特幣數據。"
                except Exception:
                    ans = "查詢比特幣時發生異常。"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ans)])
                )
            elif 客戶輸入的文字 in ["美元", "美金", "USD", "usd", "日圓", "日幣", "JPY", "jpy", "匯率"]:
                try:
                    curr_code = "JPYTWD=X" if 客戶輸入的文字 in ["日圓", "日幣", "JPY", "jpy"] else "USDTWD=X"
                    curr_name = "日圓 (JPY/TWD)" if "JPY" in curr_code else "美元 (USD/TWD)"
                    fx = yf.Ticker(curr_code)
                    df = fx.history(period="5d")
                    if not df.empty and len(df) >= 2:
                        cp = round(df.iloc[-1]["Close"], 4)
                        pc = df.iloc[-2]["Close"]
                        cpct = ((cp - pc) / pc) * 100
                        sign = "+" if cpct > 0 else ""
                        ans = f"💱【{curr_name}】最新匯率資訊\n------------------------------------\n🔹 匯率：{cp} 新台幣 (TWD)\n🔹 今日變動率：{sign}{cpct:.2f}%\n------------------------------------"
                    else:
                        ans = f"暫時無法取得匯率數據。"
                except Exception:
                    ans = "查詢匯率時發生異常。"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ans)])
                )
            else:
                stock_code = 客戶輸入的文字.upper()
                buttons_template = ButtonsTemplate(
                    title=f"【查詢 {stock_code} 表現】",
                    text="請選擇想查看的時間區間：",
                    actions=[
                        MessageAction(label="📅 一週表現", text=f"{stock_code}_1wk"),
                        MessageAction(label="📊 一個月表現", text=f"{stock_code}_1mo"),
                        MessageAction(label="📈 三個月表現", text=f"{stock_code}_3mo"),
                        MessageAction(label="🚀 年初至今 (YTD)", text=f"{stock_code}_ytd")
                    ]
                )
                template_message = TemplateMessage(
                    alt_text=f"請選擇 {stock_code} 的查詢時間區間",
                    template=buttons_template
                )
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[template_message]
                    )
                )


# ================= 啟動伺服器 =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
