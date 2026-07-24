import unittest
from unittest.mock import patch

from exchanges.base import FundingRate
from core.scanner import find_opportunities


def _bb_fr(sym, fr=0.01, mark=100, idx=100, bid=99, ask=101):
    return {sym: FundingRate(
        symbol=sym, raw_symbol=sym.replace("/", "").replace(":USDT", "") + "USDT",
        funding_rate=fr, next_payment_rate=fr,
        mark_price=mark, index_price=idx,
        funding_next_time=1700000000000, interval_hours=8,
        bid_price=bid, ask_price=ask,
    )}

def _kc_fr(sym, fr=0.005, mark=101, idx=101):
    return {sym: FundingRate(
        symbol=sym, raw_symbol=sym.split("/")[0].replace("BTC", "XBT") + "USDTM",
        funding_rate=fr, next_payment_rate=fr,
        mark_price=mark, index_price=idx,
        funding_next_time=1700000000000, interval_hours=8,
    )}


class ScannerRestBidAskTests(unittest.TestCase):
    def test_bybit_bid_ask_used_in_spread(self):
        bb = _bb_fr("BTC/USDT:USDT", fr=0.02, bid=99, ask=101)
        kc = _kc_fr("BTC/USDT:USDT", fr=0.01, mark=100)
        opps = find_opportunities(bb, kc)
        self.assertEqual(len(opps), 1)
        opp = opps[0]
        self.assertEqual(opp["bybit_bid"], 99.0)
        self.assertEqual(opp["bybit_ask"], 101.0)
        # Direction: SHORT BB / LONG KC -> p_short=bybit_bid, p_long=kucoin_ask
        # KuCoin has no bid/ask, falls back to mark=100
        expected = ((100 - 99) / 99) * 100.0
        self.assertAlmostEqual(opp["spread_pct"], expected, places=4)

    def test_kucoin_bid_ask_falls_back_to_mark(self):
        bb = _bb_fr("ETH/USDT:USDT", fr=-0.02, bid=200, ask=202)
        kc = _kc_fr("ETH/USDT:USDT", fr=-0.01, mark=201)
        opps = find_opportunities(bb, kc)
        opp = opps[0]
        self.assertEqual(opp["kucoin_bid"], 201.0)
        self.assertEqual(opp["kucoin_ask"], 201.0)

    def test_spread_both_directions(self):
        bb = _bb_fr("BTC/USDT:USDT", fr=0.02, bid=99, ask=101)
        kc = _kc_fr("BTC/USDT:USDT", fr=0.01, mark=100)
        opps = find_opportunities(bb, kc)
        opp = opps[0]
        self.assertEqual(opp["direction"], "SHORT Bybit / LONG KuCoin")

        bb2 = _bb_fr("BTC/USDT:USDT", fr=0.01, bid=99, ask=101)
        kc2 = _kc_fr("BTC/USDT:USDT", fr=0.02, mark=100)
        opps2 = find_opportunities(bb2, kc2)
        opp2 = opps2[0]
        self.assertEqual(opp2["direction"], "SHORT KuCoin / LONG Bybit")

    def test_opp_dict_includes_bid_ask_fields(self):
        bb = _bb_fr("BTC/USDT:USDT", bid=99, ask=101)
        kc = _kc_fr("BTC/USDT:USDT", mark=100)
        opps = find_opportunities(bb, kc)
        opp = opps[0]
        self.assertIn("bybit_bid", opp)
        self.assertIn("bybit_ask", opp)
        self.assertIn("kucoin_bid", opp)
        self.assertIn("kucoin_ask", opp)

    def test_zero_bybit_bid_ask_falls_back_to_mark(self):
        bb = _bb_fr("BTC/USDT:USDT", bid=0, ask=0, mark=100)
        kc = _kc_fr("BTC/USDT:USDT", mark=101)
        opps = find_opportunities(bb, kc)
        opp = opps[0]
        self.assertEqual(opp["bybit_bid"], 100.0)
        self.assertEqual(opp["bybit_ask"], 100.0)

if __name__ == "__main__":
    unittest.main()
