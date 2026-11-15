from unittest.mock import patch

import pytest

from news import news_recipients


def test_news_recipients_uses_all_registered_customers():
    expected = ["U" + "1" * 32, "U" + "2" * 32]

    with patch("google_sheet_registry.get_customer_ids", return_value=expected):
        assert news_recipients() == expected


def test_news_recipients_returns_empty_registry_as_empty():
    with patch("google_sheet_registry.get_customer_ids", return_value=[]):
        assert news_recipients() == []


def test_news_recipients_fails_closed_when_registry_is_unavailable():
    with patch(
        "google_sheet_registry.get_customer_ids",
        side_effect=RuntimeError("registry unavailable"),
    ):
        with pytest.raises(RuntimeError, match="registry unavailable"):
            news_recipients()
