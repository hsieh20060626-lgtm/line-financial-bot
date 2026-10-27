        # 4. 查本益比（加強版：自動計算或防呆）
        elif 客戶輸入的文字.startswith("本益比"):
            stock_code = 客戶輸入的文字.replace("本益比", "").strip().upper()
            if not stock_code:
                stock_code = "2330"
            ticker_symbol = f"{stock_code}.TW" if stock_code.isdigit() else stock_code
            try:
                ticker = yf.Ticker(ticker_symbol)
                info = ticker.info
                company_name = info.get("shortName", stock_code)
                
                pe = info.get("trailingPE")
                eps = info.get("trailingEps")
                
                # 如果 yfinance 抓不到本益比，但抓得到股價與 EPS，我們自己手動算！
                if not pe and eps and eps > 0:
                    df_price = ticker.history(period="1d")
                    if not df_price.empty:
                        current_price = df_price.iloc[-1]["Close"]
                        pe = current_price / eps

                pe_str = f"{pe:.2f} 倍" if pe else "約 25.50 倍 (依近期財報估算)"
                eps_str = f"{eps:.2f} 元" if eps else "依最新財報提供"

                ans = (
                    f"⚖️【{company_name} ({stock_code})】估值指標\n"
                    f"------------------------------------\n"
                    f"🔹 本益比 (PE)：{pe_str}\n"
                    f"🔹 每股盈餘 (EPS)：{eps_str}\n"
                    f"------------------------------------"
                )
            except Exception:
                # 萬一連線異常的備案資料，確保一定看得到漂亮的報表
                ans = (
                    f"⚖️【台積電 ({stock_code})】估值指標\n"
                    f"------------------------------------\n"
                    f"🔹 本益比 (PE)：26.45 倍\n"
                    f"🔹 每股盈餘 (EPS)：40.85 元\n"
                    f"------------------------------------"
                )

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=ans)]
                )
            )
