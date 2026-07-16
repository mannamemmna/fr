"""/rebalance transfers — display icon fix tests.

Ensures the green 🟢 icon shows for genuinely completed transfers
(internal_transfer_complete), not the never-written "withdraw_complete".
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message
from telegram.ext import ContextTypes


class RebalanceTransfersDisplayTests(unittest.TestCase):
    def _run_transfers(self, transfer_rows: list[dict]):
        from handlers.rebalance import cmd_rebalance
        import handlers.state as state

        mock_engine = MagicMock()
        mock_engine._rebalance_engine = MagicMock()
        mock_engine._rebalance_engine.get_status.return_value = {
            "bybit_balance": 500, "kucoin_balance": 500, "total": 1000,
            "ratio_bybit": 50.0, "ratio_kucoin": 50.0,
            "is_balanced": True, "is_rebalancing": False,
            "from_exchange": "bybit", "to_exchange": "kucoin",
            "amount_to_transfer": 0, "threshold": 0.4,
        }
        state.auto_engine = mock_engine

        mock_db = MagicMock()
        mock_db.get_recent_transfers.return_value = transfer_rows

        update = MagicMock(spec=Update)
        message = AsyncMock(spec=Message)
        update.message = message
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["transfers"]

        with patch("core.db.get_db", return_value=mock_db):
            asyncio.run(cmd_rebalance(update, context))

        call_args = message.reply_text.call_args
        if call_args.kwargs.get("text"):
            return call_args.kwargs["text"]
        return call_args.args[0] if call_args.args else ""

    def test_completed_transfer_shows_green(self):
        """internal_transfer_complete → 🟢"""
        text = self._run_transfers([
            {"type": "internal_transfer_complete", "client_id": "abc123",
             "from": "bybit", "to": "kucoin", "amount": 50.0}
        ])
        self.assertIn("🟢", text)
        self.assertNotIn("🟡", text)

    def test_failed_transfer_shows_red(self):
        """Regression: *_failed → 🔴"""
        text = self._run_transfers([
            {"type": "withdraw_call_failed", "client_id": "abc123",
             "from": "bybit", "to": "kucoin", "amount": 50.0}
        ])
        self.assertIn("🔴", text)

    def test_in_progress_transfer_shows_yellow(self):
        """Regression: non-terminal → 🟡"""
        text = self._run_transfers([
            {"type": "withdraw_submitted", "client_id": "abc123",
             "from": "bybit", "to": "kucoin", "amount": 50.0}
        ])
        self.assertIn("🟡", text)

    def test_old_withdraw_complete_string_falls_through_to_yellow(self):
        """The old, never-actually-written 'withdraw_complete' string now
        falls through to 🟡 instead of falsely showing 🟢."""
        text = self._run_transfers([
            {"type": "withdraw_complete", "client_id": "abc123",
             "from": "bybit", "to": "kucoin", "amount": 50.0}
        ])
        self.assertIn("🟡", text)
        self.assertNotIn("🟢", text)


if __name__ == "__main__":
    unittest.main()