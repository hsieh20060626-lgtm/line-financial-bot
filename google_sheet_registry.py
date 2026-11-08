"""Google Apps Script bridge for the existing LINE customer spreadsheet.

The Apps Script deployment URL is supplied through GOOGLE_SHEET_WEB_APP_URL.
Legacy contracts:
  POST {"action": "add", "userId": "<LINE user id>"} registers a user.
  POST {"action": "get"} returns {"userIds": ["U...", ...]} for broadcasts.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)


def _endpoint():
    endpoint = os.getenv("GOOGLE_SHEET_WEB_APP_URL", "").strip()
    if not endpoint:
        raise RuntimeError("GOOGLE_SHEET_WEB_APP_URL 未設定")
    return endpoint


def register_customer(user_id, session=None):
    """Register/update a LINE user. Failures are non-fatal to webhook replies."""
    if not isinstance(user_id, str) or not user_id.strip():
        return False

    try:
        client = session or requests
        response = client.post(
            _endpoint(),
            json={"action": "add", "userId": user_id.strip()},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or payload.get("verified") is not True
        ):
            logger.warning("Google 試算表客戶名單寫入未通過驗證")
            return False

        if payload.get("added") is True:
            logger.info("Google 試算表客戶名單新增並驗證成功")
        else:
            logger.info("Google 試算表客戶 UID 已存在，讀取驗證成功")
        return True
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        # Never log the endpoint, response body, or LINE User ID.
        logger.warning("Google 試算表客戶名單寫入失敗：%s", type(exc).__name__)
        return False


def get_customer_list_details(session=None):
    """Return valid unique UIDs and safe list statistics for broadcast auditing."""
    client = session or requests
    try:
        response = client.post(_endpoint(), json={"action": "get"}, timeout=8)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Google 試算表名單讀取失敗：{type(exc).__name__}") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("userIds"), list):
        raise RuntimeError("Google 試算表名單回傳格式不符，已停止推播")

    raw_ids = payload["userIds"]
    ids, seen = [], set()
    invalid_count = 0
    duplicate_count = 0
    for value in raw_ids:
        if not isinstance(value, str):
            invalid_count += 1
            continue
        uid = value.strip()
        if not uid.startswith("U") or len(uid) < 10:
            invalid_count += 1
            continue
        if uid in seen:
            duplicate_count += 1
            continue
        seen.add(uid)
        ids.append(uid)

    return {
        "user_ids": ids,
        "total_rows": len(raw_ids),
        "invalid_count": invalid_count,
        "duplicate_count": duplicate_count,
    }


def get_customer_ids(session=None):
    """Backward-compatible helper returning only valid unique LINE IDs."""
    return get_customer_list_details(session=session)["user_ids"]


def claim_announcement(event_id, user_id, announcement_text, session=None):
    """Atomically claim an announcement in Google Sheets; fail closed on errors."""
    if not isinstance(user_id, str) or not user_id.startswith("U"):
        raise ValueError("管理員 UID 格式不正確")
    if not isinstance(announcement_text, str) or not announcement_text.strip():
        raise ValueError("公告內容不可為空")

    import hashlib
    event_id = str(event_id or "").strip()
    if not event_id:
        raise ValueError("缺少 LINE webhook event ID")

    announcement_key = hashlib.sha256(
        (user_id + "\\0" + announcement_text.strip()).encode("utf-8")
    ).hexdigest()

    client = session or requests
    try:
        response = client.post(
            _endpoint(),
            json={
                "action": "claim_announcement",
                "eventId": event_id,
                "announcementKey": announcement_key,
                "userId": user_id,
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"公告防重複紀錄服務無法確認：{type(exc).__name__}"
        ) from None

    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError("公告防重複紀錄服務回傳格式錯誤")
    return payload.get("claimed") is True
