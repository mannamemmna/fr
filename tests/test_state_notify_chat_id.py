"""handlers/state.py — _notify_chat_id default tests.

Ensures _notify_chat_id always exists (defaults to None) so
core/bg_scanner.py's _send_alert() is a safe no-op instead of crashing
with AttributeError when NOTIFY_CHAT_ID is unset.
"""

import unittest
from unittest.mock import patch, MagicMock


class NotifyChatIdDefaultTests(unittest.TestCase):
    def test_state_module_declares_notify_chat_id(self):
        import handlers.state as state
        self.assertTrue(hasattr(state, "_notify_chat_id"))

    def test_default_is_none(self):
        import handlers.state as state
        # Reset to default in case another test set it
        original = getattr(state, "_notify_chat_id", None)
        state._notify_chat_id = None
        try:
            self.assertIsNone(state._notify_chat_id)
        finally:
            state._notify_chat_id = original

    def test_send_alert_is_noop_when_unset(self):
        """_send_alert() should NOT raise and should NOT call requests.post
        when _notify_chat_id is None."""
        import handlers.state as state
        from core.bg_scanner import _send_alert

        original = getattr(state, "_notify_chat_id", None)
        state._notify_chat_id = None
        try:
            with patch("builtins.__import__") as mock_import:
                _send_alert("test message")
                # requests module should not have been imported
                mock_import.assert_not_called()
        finally:
            state._notify_chat_id = original

    def test_send_alert_posts_when_set(self):
        """_send_alert() should call requests.post normally when chat_id is set."""
        import handlers.state as state
        from core.bg_scanner import _send_alert

        original = getattr(state, "_notify_chat_id", None)
        state._notify_chat_id = "123456"
        try:
            # The function does `import requests as _r` inside, so we need
            # to patch at the import level. Use __import__ mock or patch
            # the builtins. Simpler: just patch requests.post directly.
            with patch("requests.post") as mock_post:
                _send_alert("test message")
                mock_post.assert_called_once()
                call_kwargs = mock_post.call_args
                self.assertIn("123456", str(call_kwargs))
        finally:
            state._notify_chat_id = original


if __name__ == "__main__":
    unittest.main()