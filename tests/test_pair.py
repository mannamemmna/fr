"""/pair symbol matching tests — rstrip("USDT") bug fix.

Ensures symbols ending in U/S/D/T are matched correctly when the user
queries with the USDT suffix (e.g. /pair DOTUSDT → DOT, not "DO").
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message
from telegram.ext import ContextTypes


class PairMatchTests(unittest.TestCase):
    def _make_payload(self, symbols: list[str]):
        return {
            "opportunities": [
                {"symbol": s, "unified_symbol": f"{s}/USDT:USDT",
                 "spread_pct": -0.05, "direction": "SHORT-BB / LONG-KC",
                 "annual_pct": 100.0, "funding_diff_pct": 0.5,
                 "bybit_rate_pct": 0.01, "kucoin_rate_pct": -0.005,
                 "bybit_next_ts": 1700000000, "kucoin_next_ts": 1700000100,
                 "bybit_interval_h": 8, "kucoin_interval_h": 8,
                 "bybit_next_payment_pct": 0.01, "kucoin_next_payment_pct": -0.005,
                 "raw_fr_diff": 1.5, "net_daily_pct": 0.1, "diff_daily_pct": 0.5,
                 "bybit_mark": 100.0, "kucoin_mark": 101.0,
                 "bybit_action": "SHORT", "kucoin_action": "LONG"}
                for s in symbols
            ]
        }

    def _run_pair(self, query: str, symbols: list[str]):
        from handlers.pair import cmd_pair
        import handlers.state as state
        payload = self._make_payload(symbols)
        with patch("handlers.pair.read_opportunities", return_value=payload):
            update = MagicMock(spec=Update)
            message = AsyncMock(spec=Message)
            message.edit_text = AsyncMock()
            update.message = message
            context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            context.args = query.split()
            asyncio.run(cmd_pair(update, context))
        # reply_text is called with text as first positional arg
        call_args = message.reply_text.call_args
        if call_args.kwargs.get("text"):
            return call_args.kwargs["text"]
        return call_args.args[0] if call_args.args else ""

    def test_plain_base_symbol(self):
        """Regression: /pair BTC (no suffix) still works."""
        text = self._run_pair("BTC", ["BTC", "ETH"])
        self.assertIn("BTC Detail", text)

    def test_dotusdt_finds_dot(self):
        """The reported case: /pair DOTUSDT was broken by rstrip."""
        text = self._run_pair("DOTUSDT", ["DOT", "BTC"])
        self.assertIn("DOT Detail", text)

    def test_gasusdt_finds_gas(self):
        """Second affected symbol: GAS → rstrip("USDT") → "GA"."""
        text = self._run_pair("GASUSDT", ["GAS", "BTC"])
        self.assertIn("GAS Detail", text)

    def test_btcusdt_still_works(self):
        """Unaffected symbol: BTC → rstrip("USDT") → "BTC" (by coincidence)."""
        text = self._run_pair("BTCUSDT", ["BTC"])
        self.assertIn("BTC Detail", text)

    def test_unrelated_symbol_not_found(self):
        """Completely unrelated query still reports not found."""
        text = self._run_pair("NOTASYSMBOL", ["BTC", "ETH"])
        self.assertIn("tidak ditemukan", text)


if __name__ == "__main__":
    unittest.main()