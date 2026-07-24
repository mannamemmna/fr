"""
Integration tests for _calculate_price_spread and _get_scan Bug B fix.
"""

import unittest
from unittest.mock import MagicMock, patch

from core.automation_engine import AutomationEngine
from core.paper_engine import PaperEngine
from core.market_cache import PriceCache, FundingCache


class CalculatePriceSpreadTests(unittest.TestCase):
    def _call_spread(self, opp, side_bb, side_kc):
        eng = AutomationEngine(PaperEngine())
        return eng._calculate_price_spread(opp, side_bb, side_kc)

    def test_uses_bid_ask_not_mark(self):
        opp = {"bybit_bid": 99, "bybit_ask": 101, "kucoin_bid": 98, "kucoin_ask": 102,
               "bybit_mark": 500, "kucoin_mark": 500}
        result = self._call_spread(opp, "sell", "buy")
        expected = ((102 - 99) / 99) * 100.0
        self.assertAlmostEqual(result, expected, places=4)

    def test_short_kucoin_long_bybit(self):
        opp = {"bybit_bid": 99, "bybit_ask": 101, "kucoin_bid": 98, "kucoin_ask": 102,
               "bybit_mark": 100, "kucoin_mark": 100}
        result = self._call_spread(opp, "buy", "sell")
        expected = ((101 - 98) / 98) * 100.0
        self.assertAlmostEqual(result, expected, places=4)

    def test_side_explicit_not_reversed_on_flip(self):
        opp = {"bybit_bid": 99, "bybit_ask": 101, "kucoin_bid": 98, "kucoin_ask": 102,
               "spread_pct": -1.0, "direction": "SHORT KuCoin / LONG Bybit"}
        result = self._call_spread(opp, "sell", "buy")
        expected = ((102 - 99) / 99) * 100.0
        self.assertAlmostEqual(result, expected, places=4)

    def test_zero_bid_ask_returns_zero(self):
        opp = {"bybit_bid": 0, "bybit_ask": 0, "kucoin_bid": 0, "kucoin_ask": 0}
        result = self._call_spread(opp, "sell", "buy")
        self.assertEqual(result, 0.0)

    def test_partial_missing_data_returns_zero(self):
        opp = {"bybit_bid": 0, "bybit_ask": 101, "kucoin_bid": 98, "kucoin_ask": 102}
        result = self._call_spread(opp, "sell", "buy")
        self.assertEqual(result, 0.0)


class GetScanFixTests(unittest.TestCase):
    def setUp(self):
        """Reset AutomationEngine's global caches for clean state."""
        self._pc = PriceCache()
        self._fc = FundingCache()

    def _make_engine(self):
        eng = AutomationEngine(PaperEngine())
        eng._price = self._pc
        eng._funding = self._fc
        return eng

    def test_get_scan_returns_empty_list_when_no_data(self):
        eng = self._make_engine()
        with patch("core.automation_engine.read_opportunities",
                   return_value={"opportunities": []}):
            result = eng._get_scan()
        self.assertEqual(result, [])

    def test_get_scan_falls_through_to_rest_when_ws_result_empty(self):
        eng = self._make_engine()
        self._pc.update("bybit", "BTC/USDT:USDT", mark=100)
        with patch("core.automation_engine.read_opportunities",
                   return_value={"opportunities": [{"symbol": "BTC", "delta_pct": 1.0}]}):
            with patch("core.automation_engine.run_scan"):
                result = eng._get_scan()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "BTC")

    def test_get_scan_returns_ws_result_when_non_empty(self):
        eng = self._make_engine()
        fake_signal = {
            "symbol": "BTC/USDT:USDT", "bybit_price": 100, "kucoin_price": 101,
            "bybit_bid": 99, "bybit_ask": 101, "kucoin_bid": 98, "kucoin_ask": 102,
            "price_spread_pct": -1.0, "bybit_rate_pct": 0.01, "kucoin_rate_pct": -0.005,
            "bybit_next_ts": 1700000000, "kucoin_next_ts": 1700000100,
            "bybit_interval_h": 8, "kucoin_interval_h": 8,
            "bybit_action": "SHORT", "kucoin_action": "LONG",
            "funding_diff_pct": 1.5, "raw_fr_diff": 1.5,
            "annual_pct": 100, "net_daily_pct": 10, "diff_daily_pct": 5,
        }
        mock_spread = MagicMock()
        mock_spread.compute_signal.return_value = fake_signal
        eng._spread = mock_spread
        self._pc.update("bybit", "BTC/USDT:USDT", bid=100, ask=101)
        with patch("core.automation_engine.read_opportunities") as mock_read:
            result = eng._get_scan()
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["symbol"], "BTC")
        self.assertEqual(result[0]["bybit_bid"], 99)
        self.assertEqual(result[0]["bybit_ask"], 101)
        self.assertEqual(result[0]["kucoin_bid"], 98)
        self.assertEqual(result[0]["kucoin_ask"], 102)

    def test_get_scan_passes_bid_ask_fields(self):
        eng = self._make_engine()
        fake_signal = {
            "symbol": "ETH/USDT:USDT", "bybit_price": 200, "kucoin_price": 201,
            "bybit_bid": 199, "bybit_ask": 201, "kucoin_bid": 198, "kucoin_ask": 202,
            "price_spread_pct": 0.5, "bybit_rate_pct": 0.01, "kucoin_rate_pct": -0.005,
            "bybit_next_ts": 1700000000, "kucoin_next_ts": 1700000100,
            "bybit_interval_h": 8, "kucoin_interval_h": 8,
            "bybit_action": "SHORT", "kucoin_action": "LONG",
            "funding_diff_pct": 1.5, "raw_fr_diff": 1.5,
            "annual_pct": 100, "net_daily_pct": 10, "diff_daily_pct": 5,
        }
        mock_spread = MagicMock()
        mock_spread.compute_signal.return_value = fake_signal
        eng._spread = mock_spread
        self._pc.update("bybit", "ETH/USDT:USDT", bid=200, ask=201)
        result = eng._get_scan()
        self.assertGreater(len(result), 0)
        sig = result[0]
        self.assertIn("bybit_bid", sig)
        self.assertIn("bybit_ask", sig)
        self.assertIn("kucoin_bid", sig)
        self.assertIn("kucoin_ask", sig)
        self.assertEqual(sig["bybit_bid"], 199)
        self.assertEqual(sig["bybit_ask"], 201)
        self.assertEqual(sig["kucoin_bid"], 198)
        self.assertEqual(sig["kucoin_ask"], 202)


if __name__ == "__main__":
    unittest.main()