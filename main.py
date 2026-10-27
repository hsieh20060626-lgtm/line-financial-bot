
def query_valuation(stock_id):
    import math

    data = get_stock_data(stock_id)
    info = data.get("info") or {}
    stock = data["stock"]
    price = float(data["price"])

    def to_number(value):
        """將有效數字轉成 float；缺漏、NaN 或無效值回傳 None。"""
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def get_info_number(*keys):
        for key in keys:
            value = to_number(info.get(key))
            if value is not None:
                return value
        return None

    def get_statement_value(statement, row_names):
        """從財報中依照指定的列名稱，取得第一個有效數字。"""
        if statement is None or getattr(statement, "empty", True):
            return None

        rows = {
            str(index).strip().lower(): index
            for index in statement.index
        }

        for name in row_names:
            actual_row = rows.get(name.lower())
            if actual_row is None:
                continue

            for value in statement.loc[actual_row].tolist():
                number = to_number(value)
                if number is not None:
                    return number

        return None

    # 1. 先讀取基本面欄位
    eps = get_info_number("trailingEps", "epsTrailingTwelveMonths")
    pe = get_info_number("trailingPE")
    forward_pe = get_info_number("forwardPE")

    # 2. 本益比缺漏時，嘗試取得 Yahoo Finance 估值資料
    if pe is None or forward_pe is None:
        try:
            measures = stock.get_valuation_measures(
                freq="trailing",
                periods=1
            )

            if measures is not None and not measures.empty:
                rows = {
                    str(index).strip().lower(): index
                    for index in measures.index
                }

                def get_measure(*names):
                    for name in names:
                        row = rows.get(name.lower())
                        if row is None:
                            continue

                        # 優先讀取 Current 欄位
                        if "Current" in measures.columns:
                            value = to_number(measures.loc[row, "Current"])
                            if value is not None:
                                return value

                        # 若沒有 Current，嘗試第一個有效欄位
                        for value in measures.loc[row].tolist():
                            number = to_number(value)
                            if number is not None:
                                return number

                    return None

                if pe is None:
                    pe = get_measure(
                        "Trailing P/E",
                        "Trailing PE"
                    )

                if forward_pe is None:
                    forward_pe = get_measure(
                        "Forward P/E",
                        "Forward PE"
                    )

        except Exception:
            app.logger.exception(
                "取得 %s 的估值資料失敗",
                stock_id
            )

    # 3. EPS 缺漏時，先嘗試過去 12 個月財報
    if eps is None:
        try:
            ttm_statement = stock.ttm_income_stmt
            eps = get_statement_value(
                ttm_statement,
                ["Diluted EPS", "Basic EPS"]
            )
        except Exception:
            app.logger.exception(
                "取得 %s 的 TTM EPS 失敗",
                stock_id
            )

    # 4. TTM 財報沒有 EPS 時，嘗試最近四季 EPS 加總
    if eps is None:
        try:
            quarterly = stock.quarterly_income_stmt

            if quarterly is not None and not quarterly.empty:
                rows = {
                    str(index).strip().lower(): index
                    for index in quarterly.index
                }

                for row_name in ["diluted eps", "basic eps"]:
                    actual_row = rows.get(row_name)
                    if actual_row is None:
                        continue

                    values = [
                        to_number(value)
                        for value in quarterly.loc[actual_row].tolist()
                    ]
                    values = [
                        value for value in values
                        if value is not None
                    ]

                    if len(values) >= 4:
                        eps = sum(values[:4])
                        break

        except Exception:
            app.logger.exception(
                "取得 %s 的季度 EPS 失敗",
                stock_id
            )

    # 5. EPS 有效且為正數時，才用股價 / EPS 補算本益比
    if pe is None and eps is not None and eps > 0:
        pe = price / eps

    # 6. 整理顯示文字
    if eps is None:
        eps_text = "暫無資料"
    else:
        eps_text = f"{eps:.2f}"

    if pe is None:
        pe_text = "暫無資料"
    elif eps is not None and eps <= 0:
        pe_text = "不適用（EPS 非正數）"
    else:
        pe_text = f"{pe:.2f} 倍"

    forward_text = (
        f"{forward_pe:.2f} 倍"
        if forward_pe is not None
        else "暫無預估資料"
    )

    return (
        f"📊 {data['name']}｜估值資訊\n"
        f"股票代碼：{data['ticker']}\n"
        f"最新可取得收盤價：{price:.2f}"
        f"{data['currency']}\n"
        f"本益比（過去12個月）：{pe_text}\n"
        f"預估本益比：{forward_text}\n"
        f"每股盈餘 EPS（過去12個月）：{eps_text}\n\n"
        f"資料來源：Yahoo Finance\n"
        f"※ 股價可能延遲；財報更新時間與市場報價不同。"
    )
