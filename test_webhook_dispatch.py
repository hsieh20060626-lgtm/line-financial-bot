"""Safety tests for LINE webhook dispatch; no LINE API calls are made."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test-channel-secret")
os.environ.setdefault("ADMIN_USER_ID", "U123456789012345678901234")


import app as bot  # noqa: E402


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

    def test_announcement_command_remains_disabled(self):
        with patch.object(bot.MessagingApi, "multicast") as multicast:
            message, _ = bot.handle_command(
                "公告確認 測試公告不得發送",
                user_id=bot.ADMIN_USER_ID,
            )
        self.assertIn("暫停中", message)
        multicast.assert_not_called()


if __name__ == "__main__":
    unittest.main()
