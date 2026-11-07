"""Safety tests for LINE webhook dispatch and announcement idempotency.

All LINE API and Google Sheets calls are mocked; tests never contact customers.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test-channel-secret")
os.environ.setdefault("ADMIN_USER_ID", "U123456789012345678901234")

import app as bot  # noqa: E402


class CompletedFuture:
    def __init__(self, value=None):
        self.value = value

    def result(self, timeout=None):
        return self.value


class WebhookDispatchTests(unittest.TestCase):
    def test_message_webhook_only_enqueues_background_work(self):
        event = SimpleNamespace()
        with patch.object(bot._event_executor, "submit") as submit:
            result = bot.handle_message(event)
        self.assertIsNone(result)
        submit.assert_called_once_with(bot._process_message_event, event)

    def test_postback_webhook_only_enqueues_background_work(self):
        event = SimpleNamespace()
        with patch.object(bot._event_executor, "submit") as submit:
            result = bot.handle_postback(event)
        self.assertIsNone(result)
        submit.assert_called_once_with(bot._process_postback_event, event)

    def test_persistent_claim_blocks_same_announcement_with_new_event_id(self):
        text = "測試公告不得重複發送"
        uid = bot.ADMIN_USER_ID
        events = [
            SimpleNamespace(
                message=SimpleNamespace(text="公告確認 " + text),
                source=SimpleNamespace(user_id=uid),
                webhook_event_id="unit-test-event-1",
                reply_token="reply-token-1",
            ),
            SimpleNamespace(
                message=SimpleNamespace(text="公告確認 " + text),
                source=SimpleNamespace(user_id=uid),
                webhook_event_id="unit-test-event-2",
                reply_token="reply-token-2",
            ),
        ]

        def inline_submit(fn, *args, **kwargs):
            if fn is bot.register_customer:
                return CompletedFuture(None)
            return CompletedFuture(fn(*args, **kwargs))

        with (
            patch("google_sheet_registry.claim_announcement", side_effect=[True, False]),
            patch("google_sheet_registry.get_customer_ids", return_value=["U123456789012345678901234"]),
            patch.object(bot._executor, "submit", side_effect=inline_submit),
            patch.object(bot, "send_reply"),
            patch.object(bot, "record_usage"),
            patch.object(bot.MessagingApi, "multicast") as multicast,
        ):
            bot._process_message_event(events[0])
            bot._process_message_event(events[1])

        self.assertEqual(multicast.call_count, 1)

    def test_persistent_claim_failure_fails_closed(self):
        event = SimpleNamespace(
            message=SimpleNamespace(text="公告確認 不可發送"),
            source=SimpleNamespace(user_id=bot.ADMIN_USER_ID),
            webhook_event_id="unit-test-event-3",
            reply_token="reply-token-3",
        )
        with (
            patch("google_sheet_registry.claim_announcement", side_effect=RuntimeError("sheet unavailable")),
            patch.object(bot, "send_reply"),
            patch.object(bot, "record_usage"),
            patch.object(bot.MessagingApi, "multicast") as multicast,
        ):
            bot._process_message_event(event)
        multicast.assert_not_called()


if __name__ == "__main__":
    unittest.main()
