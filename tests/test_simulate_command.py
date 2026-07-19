"""/simulate command — end-to-end tests driving the real cmd_simulate handler."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message
from telegram.ext import ContextTypes


class SimulateCommandTests(unittest.TestCase):
    def setUp(self):
        import handlers.state as state
        self.mock_engine = MagicMock()
        state.paper_engine = self.mock_engine

    def _run_simulate(self, args: list[str], paper_mode=True):
        from handlers.simulate import cmd_simulate

        update = MagicMock(spec=Update)
        message = AsyncMock(spec=Message)
        update.message = message
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = args

        with patch("handlers.simulate.PAPER_MODE", paper_mode):
            asyncio.run(cmd_simulate(update, context))

        call_args = message.reply_text.call_args
        if call_args:
            if call_args.kwargs.get("text"):
                return call_args.kwargs["text"]
            return call_args.args[0] if call_args.args else ""
        return ""

    def test_no_args_shows_usage(self):
        text = self._run_simulate([])
        self.assertIn("Usage:", text)

    def test_one_arg_shows_usage(self):
        text = self._run_simulate(["abc123"])
        self.assertIn("Usage:", text)

    def test_refuses_in_live_mode(self):
        text = self._run_simulate(["abc123", "bybit"], paper_mode=False)
        self.assertIn("Paper Mode", text)

    def test_handles_uninitialized_engine(self):
        import handlers.state as state
        state.paper_engine = None
        text = self._run_simulate(["abc123", "bybit"])
        self.assertIn("belum siap", text.lower())

    def test_single_leg_force_close_shows_belum(self):
        self.mock_engine.force_close_leg.return_value = {
            "ok": True,
            "symbol": "BTC",
            "legs_status": {"bybit": "closed", "kucoin": "open"},
            "both_legs_closed": False,
        }
        text = self._run_simulate(["abc123", "bybit"])
        self.assertIn("LEG FORCE-CLOSED", text)
        self.assertIn("Belum", text)

    def test_both_legs_closed_shows_ya(self):
        self.mock_engine.force_close_leg.return_value = {
            "ok": True,
            "symbol": "BTC",
            "legs_status": {"bybit": "closed", "kucoin": "closed"},
            "both_legs_closed": True,
        }
        text = self._run_simulate(["abc123", "kucoin"])
        self.assertIn("Ya", text)

    def test_error_result_surfaced(self):
        self.mock_engine.force_close_leg.return_value = {
            "ok": False, "error": "bybit leg already closed"
        }
        text = self._run_simulate(["abc123", "bybit"])
        self.assertIn("already closed", text)

    def test_exchange_arg_lowercased(self):
        self.mock_engine.force_close_leg.return_value = {
            "ok": True,
            "symbol": "BTC",
            "legs_status": {"bybit": "open", "kucoin": "closed"},
            "both_legs_closed": False,
        }
        self._run_simulate(["abc123", "BYBIT"])
        self.mock_engine.force_close_leg.assert_called_with("abc123", "bybit")


if __name__ == "__main__":
    unittest.main()