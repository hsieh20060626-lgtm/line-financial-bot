import unittest
from unittest.mock import Mock, patch

import requests

from google_sheet_registry import get_customer_ids, register_customer


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

    def test_posts_legacy_add_payload(self):
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

    def test_reads_and_deduplicates_existing_user_ids(self):
        with patch.dict("os.environ", {"GOOGLE_SHEET_WEB_APP_URL": "https://example.test/exec"}):
            session = Mock()
            response = Mock()
            response.json.return_value = {"userIds": ["U1234567890", "U1234567890", "bad", "", "Uabcdefghij"]}
            session.post.return_value = response
            self.assertEqual(
                get_customer_ids(session=session),
                ["U1234567890", "Uabcdefghij"],
            )
            session.post.assert_called_once_with(
                "https://example.test/exec", json={"action": "get"}, timeout=8
            )

    def test_malformed_recipient_response_fails_closed(self):
        with patch.dict("os.environ", {"GOOGLE_SHEET_WEB_APP_URL": "https://example.test/exec"}):
            session = Mock()
            response = Mock()
            response.json.return_value = {"users": ["U1234567890"]}
            session.post.return_value = response
            with self.assertRaises(RuntimeError):
                get_customer_ids(session=session)

    def test_read_http_error_fails_closed(self):
        with patch.dict("os.environ", {"GOOGLE_SHEET_WEB_APP_URL": "https://example.test/exec"}):
            session = Mock()
            response = Mock()
            response.raise_for_status.side_effect = requests.HTTPError()
            session.post.return_value = response
            with self.assertRaises(RuntimeError):
                get_customer_ids(session=session)


if __name__ == "__main__":
    unittest.main()
