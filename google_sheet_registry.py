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


def get_customer_ids(session=None):
    """Read all registered LINE IDs; fail closed if the response is malformed."""
    client = session or requests
    try:
        response = client.post(_endpoint(), json={"action": "get"}, timeout=8)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Google 試算表名單讀取失敗：{type(exc).__name__}") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("userIds"), list):
        raise RuntimeError("Google 試算表名單回傳格式不符，已停止推播")

    # Keep only plausible LINE user ID strings and remove duplicates while
    # preserving the sheet's original order.
    ids, seen = [], set()
    for value in payload["userIds"]:
        if isinstance(value, str):
            uid = value.strip()
            if uid.startswith("U") and len(uid) >= 10 and uid not in seen:
                seen.add(uid)
                ids.append(uid)
    return ids
