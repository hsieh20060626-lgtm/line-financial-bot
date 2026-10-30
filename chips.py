"""
川投顧｜全市場法人買賣超排行（三大法人合計／外資／投信）

資料來源：台灣證券交易所「三大法人買賣超日報（T86）」，只含上市個股。
負責「抓資料並排名」，不依賴 LINE SDK；卡片由 app.py 產生。
"""

import time
import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

TW_TZ = timezone(timedelta(hours=8))

# 先用新版網址；只有在連線出錯時才改試舊版網址
T86_URLS = [
    "https://www.twse.com.tw/rwd/zh/fund/T86",
    "https://www.twse.com.tw/fund/T86",
]

TOP_N = 5               # 排行前幾名
INCLUDE_ETF = False     # False：只排一般個股（4 位數代碼）；True：連 ETF 一起排
MAX_LOOKBACK_DAYS = 7   # 今天沒資料（假日或尚未公布）時，最多往前找幾天
CACHE_SECONDS = 600

# 各種排行對應 T86 的哪一個欄位（用欄位名稱比對，證交所調整欄位順序也不怕）
COLUMN_MATCHERS = {
    "total": lambda name: "三大法人買賣超股數" in name,
    "foreign": lambda name: (
        "買賣超股數" in name
        and ("外陸資" in name or "外資及陸資" in name)
        and "自營商" not in name.replace("不含外資自營商", "")
    ),
    "trust": lambda name: "投信買賣超股數" in name,
}

_cache = {"time": 0.0, "date": None, "table": None}


def _to_int(text):
    """把「1,234」「-5,678」轉成整數；「--」之類無法轉換的當作 0。"""
    try:
        return int(str(text).replace(",", "").strip())
    except ValueError:
        return 0


def _extract_table(payload):
    """從證交所回傳中取出 (欄位名稱, 資料列)；沒有資料回傳 None。"""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return None
    if payload.get("fields") and payload.get("data"):
        return payload["fields"], payload["data"]
    for table in payload.get("tables") or []:
        if table.get("fields") and table.get("data"):
            return table["fields"], table["data"]
    return None


def _fetch_day(day):
    """抓某一天的 T86；該日沒有資料回傳 None。"""
    params = {
        "date": day.strftime("%Y%m%d"),
        "selectType": "ALLBUT0999",
        "response": "json",
    }
    for url in T86_URLS:
        try:
            response = requests.get(
                url, params=params, timeout=8, headers={"User-Agent": "Mozilla/5.0"}
            )
            response.raise_for_status()
            return _extract_table(response.json())
        except Exception as exc:
            logger.warning("證交所 T86 讀取失敗（%s，%s）：%s", url, params["date"], exc)
    return None


def _get_table():
    """取得最近一個有資料的交易日 (日期, (欄位, 資料列))，快取 10 分鐘。"""
    if _cache["table"] and time.time() - _cache["time"] < CACHE_SECONDS:
        return _cache["date"], _cache["table"]

    today = datetime.now(TW_TZ).date()
    for offset in range(MAX_LOOKBACK_DAYS + 1):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:  # 週六、週日沒有交易
            continue

        table = _fetch_day(day)
        if table:
            _cache.update(time=time.time(), date=day.isoformat(), table=table)
            return _cache["date"], table

    return None


def _rank(fields, data, matcher):
    """依指定欄位（買賣超股數）排名，回傳 (買超前幾名, 賣超前幾名)。"""
    names = [str(f).strip() for f in fields]
    try:
        i_code = names.index("證券代號")
        i_name = names.index("證券名稱")
    except ValueError:
        return None
    i_value = next((i for i, n in enumerate(names) if matcher(n)), None)
    if i_value is None:
        return None

    stocks = []
    for row in data:
        if len(row) <= max(i_code, i_name, i_value):
            continue
        code = str(row[i_code]).strip()
        if not INCLUDE_ETF and not (len(code) == 4 and code.isdigit()):
            continue
        stocks.append({
            "code": code,
            "name": str(row[i_name]).strip(),
            "net": _to_int(row[i_value]),  # 單位：股
        })

    buys = sorted((s for s in stocks if s["net"] > 0), key=lambda s: -s["net"])[:TOP_N]
    sells = sorted((s for s in stocks if s["net"] < 0), key=lambda s: s["net"])[:TOP_N]
    return buys, sells


def fetch_top_institutional(who="total"):
    """
    who：'total'（三大法人合計）、'foreign'（外資，不含外資自營商）、'trust'（投信）。
    回傳 {'date': 'YYYY-MM-DD', 'buy': [...], 'sell': [...]}，每筆是 {'code','name','net'}（net 單位為股）。
    抓不到資料回傳 None。
    """
    found = _get_table()
    if not found:
        return None

    date_text, (fields, data) = found
    ranked = _rank(fields, data, COLUMN_MATCHERS.get(who, COLUMN_MATCHERS["total"]))
    if not ranked:
        return None

    buys, sells = ranked
    return {"date": date_text, "buy": buys, "sell": sells}


# ==================================================
# 全市場三大法人買賣金額（證交所 BFI82U）
# ==================================================

BFI82U_URLS = [
    "https://www.twse.com.tw/rwd/zh/fund/BFI82U",
    "https://www.twse.com.tw/fund/BFI82U",
]

_summary_cache = {"time": 0.0, "result": None}


def _to_float(text):
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return 0.0


def _clean_name(text):
    """統一單位名稱的空白與括號寫法，方便比對。"""
    return (
        str(text).replace(" ", "").replace("\u3000", "")
        .replace("（", "(").replace("）", ")")
    )


def _fetch_summary_day(day):
    params = {"type": "day", "dayDate": day.strftime("%Y%m%d"), "response": "json"}
    for url in BFI82U_URLS:
        try:
            response = requests.get(
                url, params=params, timeout=8, headers={"User-Agent": "Mozilla/5.0"}
            )
            response.raise_for_status()
            return _extract_table(response.json())
        except Exception as exc:
            logger.warning("證交所 BFI82U 讀取失敗（%s，%s）：%s", url, params["dayDate"], exc)
    return None


def _summarize(fields, data):
    """整理成各法人買賣超金額（億元）：外資、投信、自營商、合計。"""
    names = [str(f).strip() for f in fields]
    try:
        i_name = names.index("單位名稱")
    except ValueError:
        return None
    i_net = next((i for i, n in enumerate(names) if "買賣差額" in n), None)
    if i_net is None:
        return None

    nets = {}
    for row in data:
        if len(row) > max(i_name, i_net):
            nets[_clean_name(row[i_name])] = _to_float(row[i_net])
    if not nets:
        return None

    # 證交所金額單位若是「元」就換算成「億元」（單位是億元時，數字不會這麼大）
    scale = 1e8 if max(abs(v) for v in nets.values()) >= 1e6 else 1.0

    def pick(*prefixes):
        values = [v for name, v in nets.items() if any(name.startswith(p) for p in prefixes)]
        return sum(values) / scale if values else None

    foreign_main = pick("外資及陸資", "外陸資")
    foreign_dealer = pick("外資自營商")
    trust = pick("投信")
    dealer = pick("自營商(自行買賣)", "自營商(避險)")
    if dealer is None:
        dealer = pick("自營商")  # 舊格式：自營商只有一列

    foreign = None
    if foreign_main is not None or foreign_dealer is not None:
        foreign = (foreign_main or 0.0) + (foreign_dealer or 0.0)

    if foreign is None or trust is None or dealer is None:
        return None

    total = nets["合計"] / scale if "合計" in nets else foreign + trust + dealer
    return {"foreign": foreign, "trust": trust, "dealer": dealer, "total": total}


def fetch_market_summary():
    """
    回傳 {'date', 'foreign', 'trust', 'dealer', 'total'}，金額單位為億元（正數買超、負數賣超）。
    抓不到資料回傳 None。結果快取 10 分鐘。
    """
    if _summary_cache["result"] and time.time() - _summary_cache["time"] < CACHE_SECONDS:
        return _summary_cache["result"]

    today = datetime.now(TW_TZ).date()
    for offset in range(MAX_LOOKBACK_DAYS + 1):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue

        table = _fetch_summary_day(day)
        if not table:
            continue

        summary = _summarize(*table)
        if summary:
            summary["date"] = day.isoformat()
            _summary_cache.update(time=time.time(), result=summary)
            return summary

    return None
