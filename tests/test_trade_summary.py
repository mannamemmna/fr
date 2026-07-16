"""_format_trade_summary — price spread display tests.

Ensures the auto-close trade summary message includes the price spread
movement (entry → current), which was a silent dead parameter before.
"""

import unittest

from core.automation_engine import _format_trade_summary


class TradeSummarySpreadTests(unittest.TestCase):
    def _base_result(self, **overrides):
        result = {
            "ok": True,
            "price_pnl": 0.0, "funding_pnl": 0.0,
            "fr_paid": 0.0, "fr_received": 0.0, "fees": 0.0,
            "realized_pnl": 0.0,
            "entry_price_bybit": 100, "entry_price_kucoin": 101,
            "exit_price_bybit": 100, "exit_price_kucoin": 101,
            "entry_fee_bybit": 0.055, "entry_fee_kucoin": 0.06,
            "exit_fee_bybit": 0.055, "exit_fee_kucoin": 0.06,
            "side_bybit": "sell", "side_kucoin": "buy",
            "amount_usd": 100, "leverage": 3, "position_size": 300,
        }
        result.update(overrides)
        return result

    def test_spread_line_appears(self):
        result = self._base_result()
        text = _format_trade_summary(result, "BTC", -0.05, 0.10, 0.6, 0.3)
        self.assertIn("Price Spread:", text)
        self.assertIn("-0.0500%", text)
        self.assertIn("+0.1000%", text)

    def test_different_spreads_produce_different_output(self):
        result = self._base_result()
        text_a = _format_trade_summary(result, "BTC", -0.05, 0.10, 0.6, 0.3)
        text_b = _format_trade_summary(result, "BTC", -0.20, 0.50, 0.6, 0.3)
        # The spread line must differ
        spread_lines_a = [l for l in text_a.split("\n") if "Price Spread:" in l]
        spread_lines_b = [l for l in text_b.split("\n") if "Price Spread:" in l]
        self.assertEqual(len(spread_lines_a), 1)
        self.assertEqual(len(spread_lines_b), 1)
        self.assertNotEqual(spread_lines_a[0], spread_lines_b[0])

    def test_failed_close_does_not_include_spread_line(self):
        result = {"ok": False, "error": "test error"}
        text = _format_trade_summary(result, "BTC", -0.05, 0.10, 0.6, 0.3)
        self.assertIn("AUTO CLOSE FAILED", text)
        self.assertNotIn("Price Spread:", text)


if __name__ == "__main__":
    unittest.main()