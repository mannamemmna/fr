"""/portfolio unrealized PnL — per-leg qty fix.

Ensures /portfolio uses qty_bybit and qty_kucoin separately, not a single
shared quantity, for the unrealized PnL display.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message
from telegram.ext import ContextTypes


class PortfolioUpnlTests(unittest.TestCase):
    def test_uses_per_leg_qty_not_shared_quantity(self):
        """With qty_bybit=1.0, qty_kucoin=2.0, and a price move that makes
        the two legs' contributions asymmetric, the combined uPnL must
        reflect each leg's own quantity — not a single shared one."""
        from handlers.portfolio import cmd_portfolio
        import handlers.state as state

        # Position: SHORT Bybit (qty 1.0), LONG KuCoin (qty 2.0)
        # Entry: BB=100, KC=100. Current: BB=110, KC=110
        # SHORT BB PnL: 1.0 * (100-110) = -10
        # LONG  KC PnL: 2.0 * (110-100) = +20
        # Combined: +10.00 (correct)
        # Old buggy code used qty=1.0 for both → -10 + 10 = 0.00 (wrong)
        position = {
            "id": "test-pos-1",
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "amount_usd": 100,
            "leverage": 3,
            "entry_price_bybit": 100,
            "entry_price_kucoin": 100,
            "entry_spread": -0.05,
            "qty_bybit": 1.0,
            "qty_kucoin": 2.0,
            "quantity": 1.0,  # legacy field — should NOT be used for both legs
            "funding_pnl": 0,
            "fr_paid": 0,
            "fr_received": 0,
            "status": "open",
            "paper": True,
        }

        scan_opp = {
            "symbol": "BTC",
            "bybit_mark": 110,
            "kucoin_mark": 110,
            "bybit_next_ts": 1700000000,
            "kucoin_next_ts": 1700000100,
        }

        mock_engine = MagicMock()
        mock_engine.get_summary.return_value = {
            "positions": [position],
            "balance": 1000,
            "bybit_balance": 500,
            "kucoin_balance": 500,
            "total_pnl": 0,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "total_exposure": 300,
        }
        state.paper_engine = mock_engine
        state.last_scan = {"opportunities": [scan_opp]}

        update = MagicMock(spec=Update)
        message = AsyncMock(spec=Message)
        update.message = message
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

        asyncio.run(cmd_portfolio(update, context))

        call_args = message.reply_text.call_args
        if call_args.kwargs.get("text"):
            text = call_args.kwargs["text"]
        else:
            text = call_args.args[0] if call_args.args else ""
        # Correct: +10.00 (not +0.00 which the old buggy code produced)
        self.assertIn("+10.00", text)
        # Ensure the per-leg qty fix is actually exercised: the old buggy
        # code would produce +0.00 for the unrealized PnL line specifically
        upnl_line = [l for l in text.split("\n") if "Profit/Loss saat ini" in l]
        self.assertTrue(upnl_line)
        self.assertIn("+10.00", upnl_line[0])


if __name__ == "__main__":
    unittest.main()