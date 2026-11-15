import unittest
from unittest.mock import patch

from news import news_recipients


class NewsRecipientsTests(unittest.TestCase):
    def test_news_recipients_uses_all_registered_customers(self):
        expected = ["U" + "1" * 32, "U" + "2" * 32]
        with patch("google_sheet_registry.get_customer_ids", return_value=expected):
            self.assertEqual(news_recipients(), expected)

    def test_news_recipients_returns_empty_registry_as_empty(self):
        with patch("google_sheet_registry.get_customer_ids", return_value=[]):
            self.assertEqual(news_recipients(), [])

    def test_news_recipients_fails_closed_when_registry_is_unavailable(self):
        with patch(
            "google_sheet_registry.get_customer_ids",
            side_effect=RuntimeError("registry unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                news_recipients()


if __name__ == "__main__":
    unittest.main()
