"""/scan WS subscription cap — integration tests with real cmd_scan handler.

Verifies that /scan respects MAX_WS_SUBSCRIPTIONS, matching the cap
that bot.py and core/bg_scanner.py already enforce.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message
from telegram.ext import ContextTypes


class ScanWsCapTests(unittest.TestCase):
    def setUp(self):
        self._patcher = patch("config.MAX_WS_SUBSCRIPTIONS", 3)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _make_payload(self, n_symbols: int):
        """Construct a scan payload with n_symbols unique opportunities."""
        symbols = [f"TOKEN{i}USDT" for i in range(n_symbols)]
        opps = [{"symbol": s, "spread_pct": 0.01, "direction": "SHORT-BB / LONG-KC",
                  "annual_pct": 100.0, "funding_diff_pct": 0.5,
                  "bybit_rate_pct": 0.01, "kucoin_rate_pct": -0.005,
                  "bybit_next_time": "12:00", "kucoin_next_time": "12:00",
                  "bybit_count": n_symbols, "kucoin_count": n_symbols}
                 for s in symbols]
        return {
            "opportunities": opps,
            "scan_duration": 1.0,
            "bybit_count": n_symbols,
            "kucoin_count": n_symbols,
            "common_count": n_symbols,
        }

    def test_scan_caps_at_max_ws_subscriptions(self):
        from handlers.scan import cmd_scan
        import handlers.state as state

        ws_mock = MagicMock()
        state.ws_pool = ws_mock

        payload = self._make_payload(10)  # 10 symbols, cap is 3

        with patch("handlers.scan.run_scan", return_value=payload):
            update = MagicMock(spec=Update)
            message = AsyncMock(spec=Message)
            message.edit_text = AsyncMock()
            update.message = message
            context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

            asyncio.run(cmd_scan(update, context))

        ws_mock.update_symbols.assert_called_once()
        subscribed = ws_mock.update_symbols.call_args[0][0]
        self.assertEqual(len(subscribed), 3)

    def test_scan_respects_no_cap_when_under_limit(self):
        from handlers.scan import cmd_scan
        import handlers.state as state

        ws_mock = MagicMock()
        state.ws_pool = ws_mock

        payload = self._make_payload(2)  # 2 symbols, cap is 3

        with patch("handlers.scan.run_scan", return_value=payload):
            update = MagicMock(spec=Update)
            message = AsyncMock(spec=Message)
            message.edit_text = AsyncMock()
            update.message = message
            context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

            asyncio.run(cmd_scan(update, context))

        ws_mock.update_symbols.assert_called_once()
        subscribed = ws_mock.update_symbols.call_args[0][0]
        self.assertEqual(len(subscribed), 2)


if __name__ == "__main__":
    unittest.main()