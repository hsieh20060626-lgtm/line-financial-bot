import unittest
from unittest.mock import Mock, patch

import requests

from google_sheet_registry import register_customer


class GoogleSheetRegistryTests(unittest.TestCase):
    def test_empty_user_id_is_ignored(self):
        session = Mock()
        self.assertFalse(register_customer("", session=session))
        session.post.assert_not_called()

    def test_missing_endpoint_is_ignored(self):
        with patch.dict("os.environ", {}, clear=True):
            session = Mock()
            self.assertFalse(register_customer("U-test", session=session))
            session.post.assert_not_called()

    def test_posts_legacy_apps_script_payload(self):
        with patch.dict("os.environ", {"GOOGLE_SHEET_WEB_APP_URL": "https://example.test/exec"}):
            session = Mock()
            session.post.return_value = Mock(status_code=200)
            self.assertTrue(register_customer("U-test", session=session))
            session.post.assert_called_once_with(
                "https://example.test/exec",
                json={"action": "add", "userId": "U-test"},
                timeout=5,
            )
            session.post.return_value.raise_for_status.assert_called_once()

    def test_http_error_does_not_raise_to_webhook(self):
        with patch.dict("os.environ", {"GOOGLE_SHEET_WEB_APP_URL": "https://example.test/exec"}):
            session = Mock()
            response = Mock()
            response.raise_for_status.side_effect = requests.HTTPError()
            session.post.return_value = response
            self.assertFalse(register_customer("U-test", session=session))


if __name__ == "__main__":
    unittest.main()
