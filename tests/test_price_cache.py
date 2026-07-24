import time
import unittest
from core.market_cache import PriceCache

class PriceCacheBidAskTests(unittest.TestCase):
    def test_update_mark_only_does_not_zero_bid(self):
        c = PriceCache()
        c.update("bybit", "BTC/USDT:USDT", bid=100.0, ask=101.0)
        c.update("bybit", "BTC/USDT:USDT", mark=100.5)
        bid, ask = c.get_bid_ask("bybit", "BTC/USDT:USDT")
        self.assertEqual(bid, 100.0)
        self.assertEqual(ask, 101.0)

    def test_update_bid_only_does_not_zero_mark(self):
        c = PriceCache()
        c.update("kucoin", "BTC/USDT:USDT", mark=100.5)
        c.update("kucoin", "BTC/USDT:USDT", bid=99.0)
        self.assertEqual(c.get_price("kucoin", "BTC/USDT:USDT"), 100.5)

    def test_get_bid_ask_returns_tuple(self):
        c = PriceCache()
        c.update("bybit", "BTC", bid=99.0, ask=101.0)
        bid, ask = c.get_bid_ask("bybit", "BTC")
        self.assertEqual(bid, 99.0)
        self.assertEqual(ask, 101.0)

    def test_get_bid_ask_defaults_zero(self):
        c = PriceCache()
        bid, ask = c.get_bid_ask("bybit", "NONEXIST")
        self.assertEqual(bid, 0.0)
        self.assertEqual(ask, 0.0)

    def test_get_price_returns_mark(self):
        c = PriceCache()
        c.update("bybit", "BTC", mark=100.0, bid=99.0, ask=101.0)
        self.assertEqual(c.get_price("bybit", "BTC"), 100.0)

    def test_all_symbols_returns_list(self):
        c = PriceCache()
        c.update("bybit", "BTC", mark=100)
        c.update("kucoin", "ETH", mark=50)
        syms = c.all_symbols()
        self.assertIn("BTC", syms)
        self.assertIn("ETH", syms)

    def test_age_returns_none_for_unknown(self):
        c = PriceCache()
        self.assertIsNone(c.age("NONEXIST"))

    def test_age_returns_float_for_known(self):
        c = PriceCache()
        c.update("bybit", "BTC", mark=100)
        age = c.age("BTC")
        self.assertIsInstance(age, float)
        self.assertGreaterEqual(age, 0)

    def test_update_all_none_does_nothing(self):
        c = PriceCache()
        c.update("bybit", "BTC", mark=100)
        c.update("bybit", "BTC")
        self.assertEqual(c.get_price("bybit", "BTC"), 100.0)

if __name__ == "__main__":
    unittest.main()
