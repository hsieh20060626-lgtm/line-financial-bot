"""Google Apps Script bridge for the existing LINE customer spreadsheet.

The Apps Script deployment URL is supplied through GOOGLE_SHEET_WEB_APP_URL.
The legacy contract is POST {"action": "add", "userId": "<LINE user id>"}.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)


def register_customer(user_id, session=None):
    """Register/update a LINE user in the existing Google Sheet.

    Returns True when the Apps Script endpoint accepts the request. Failures
    are deliberately non-fatal so customer message replies keep working.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        return False

    endpoint = os.getenv("GOOGLE_SHEET_WEB_APP_URL", "").strip()
    if not endpoint:
        logger.warning("GOOGLE_SHEET_WEB_APP_URL 未設定，略過客戶名單寫入")
        return False

    client = session or requests
    try:
        response = client.post(
            endpoint,
            json={"action": "add", "userId": user_id.strip()},
            timeout=5,
        )
        response.raise_for_status()
        logger.info("Google 試算表客戶名單寫入成功")
        return True
    except requests.RequestException as exc:
        # Do not log the URL, response body, or LINE User ID.
        logger.warning("Google 試算表客戶名單寫入失敗：%s", type(exc).__name__)
        return False
