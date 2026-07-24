import unittest
from unittest.mock import MagicMock, patch
from core.market_cache import PriceCache, FundingCache

class SpreadEngineBidAskTests(unittest.TestCase):
    def setUp(self):
        self.pc = PriceCache()
        self.fc = FundingCache()
        self._seed_data()

    def _seed_data(self):
        for ex in ("bybit", "kucoin"):
            self.fc.update(ex, "BTC/USDT:USDT", 0.01, 0.01, 1700000000000, 8)
        self.pc.update("bybit", "BTC/USDT:USDT", bid=99.0, ask=101.0)
        self.pc.update("kucoin", "BTC/USDT:USDT", bid=98.0, ask=102.0)

    def _compute(self):
        from core.spread_engine import SpreadEngine
        se = SpreadEngine(self.pc, self.fc)
        return se.compute_signal("BTC/USDT:USDT")

    def test_uses_bid_ask_not_mark(self):
        self.pc.update("bybit", "BTC/USDT:USDT", mark=999.0)  # mark far from bid/ask
        sig = self._compute()
        self.assertIsNotNone(sig)
        self.assertEqual(sig["bybit_bid"], 99.0)
        self.assertEqual(sig["bybit_ask"], 101.0)

    def test_spread_reflects_short_bybit_bid(self):
        # raw_fr_diff > 0 -> SHORT Bybit / LONG KuCoin
        self.fc.update("bybit", "BTC/USDT:USDT", 0.02, 0.02, 1700000000000, 8)
        self.fc.update("kucoin", "BTC/USDT:USDT", 0.01, 0.01, 1700000000000, 8)
        sig = self._compute()
        # p_short = bybit_bid (99), p_long = kucoin_ask (102)
        expected = ((102 - 99) / 99) * 100.0
        self.assertAlmostEqual(sig["price_spread_pct"], expected, places=4)
        self.assertEqual(sig["direction"], "SHORT Bybit / LONG KuCoin")

    def test_spread_reflects_short_kucoin_bid(self):
        # raw_fr_diff < 0 -> SHORT KuCoin / LONG Bybit
        self.fc.update("bybit", "BTC/USDT:USDT", 0.01, 0.01, 1700000000000, 8)
        self.fc.update("kucoin", "BTC/USDT:USDT", 0.02, 0.02, 1700000000000, 8)
        sig = self._compute()
        # p_short = kucoin_bid (98), p_long = bybit_ask (101)
        expected = ((101 - 98) / 98) * 100.0
        self.assertAlmostEqual(sig["price_spread_pct"], expected, places=4)
        self.assertEqual(sig["direction"], "SHORT KuCoin / LONG Bybit")

    def test_missing_bid_ask_returns_none(self):
        self.pc.update("bybit", "BTC/USDT:USDT", bid=0, ask=0, mark=100)
        sig = self._compute()
        self.assertIsNone(sig)

    def test_missing_one_side_returns_none(self):
        self.pc.update("bybit", "BTC/USDT:USDT", bid=99, ask=101)
        self.pc.update("kucoin", "BTC/USDT:USDT", bid=98, ask=0)  # ask=0
        sig = self._compute()
        self.assertIsNone(sig)

    def test_flat_direction_uses_bid(self):
        self.fc.update("bybit", "BTC/USDT:USDT", 0.01, 0.01, 1700000000000, 8)
        self.fc.update("kucoin", "BTC/USDT:USDT", 0.01, 0.01, 1700000000000, 8)
        sig = self._compute()
        self.assertEqual(sig["direction"], "FLAT")
        # p_short = p_long = bybit_bid
        self.assertAlmostEqual(sig["price_spread_pct"], 0.0)

    def test_signal_includes_bid_ask_fields(self):
        sig = self._compute()
        self.assertIn("bybit_bid", sig)
        self.assertIn("bybit_ask", sig)
        self.assertIn("kucoin_bid", sig)
        self.assertIn("kucoin_ask", sig)

if __name__ == "__main__":
    unittest.main()
