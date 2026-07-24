import json
import unittest
from unittest.mock import MagicMock, patch

from core.market_cache import PriceCache, FundingCache


class BybitBidAskExtractionTests(unittest.TestCase):
    def _make_bybit_handler(self):
        pc = PriceCache()
        fc = FundingCache()
        from core.ws_pool import BybitWS
        ws = BybitWS(["BTC"], pc, fc)
        return ws, pc, fc

    def test_bybit_extracts_bid_ask_and_mark_from_single_push(self):
        ws, pc, _ = self._make_bybit_handler()
        msg = json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {
                "markPrice": "100.5", "bid1Price": "100.0", "ask1Price": "101.0",
                "fundingRate": "0.01", "nextFundingTime": "1700000000000", "fundingIntervalHour": "8"
            }
        })
        ws._handle_message("bybit", msg)
        bid, ask = pc.get_bid_ask("bybit", "BTC/USDT:USDT")
        self.assertEqual(bid, 100.0)
        self.assertEqual(ask, 101.0)
        self.assertEqual(pc.get_price("bybit", "BTC/USDT:USDT"), 100.5)

    def test_bybit_mark_only_push_does_not_zero_bid(self):
        ws, pc, _ = self._make_bybit_handler()
        ws._handle_message("bybit", json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"markPrice": "100.5", "bid1Price": "100.0", "ask1Price": "101.0",
                     "fundingRate": "0.01", "nextFundingTime": "1700000000000", "fundingIntervalHour": "8"}
        }))
        ws._handle_message("bybit", json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"markPrice": "100.7"}
        }))
        bid, ask = pc.get_bid_ask("bybit", "BTC/USDT:USDT")
        self.assertEqual(bid, 100.0)
        self.assertEqual(ask, 101.0)

    def test_bybit_funding_only_push_updates_funding_cache(self):
        ws, pc, fc = self._make_bybit_handler()
        ws._handle_message("bybit", json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"fundingRate": "0.01", "nextFundingTime": "1700000000000", "fundingIntervalHour": "8"}
        }))
        f = fc.get("BTC/USDT:USDT", "bybit")
        self.assertIsNotNone(f)
        self.assertEqual(f["funding_rate"], 0.01)

    def test_malformed_json_not_raising(self):
        ws, pc, _ = self._make_bybit_handler()
        ws._handle_message("bybit", "not-json")
        self.assertEqual(pc.all_symbols(), [])


class KuCoinBidAskExtractionTests(unittest.TestCase):
    def _make_kucoin_handler(self):
        pc = PriceCache()
        fc = FundingCache()
        with patch("core.ws_pool.KuCoinWS._get_ws_url", return_value="wss://fake/"):
            from core.ws_pool import KuCoinWS
            ws = KuCoinWS(["BTC"], pc, fc)
        return ws, pc, fc

    def test_kucoin_instrument_mark_price(self):
        ws, pc, _ = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contract/instrument:XBTUSDTM",
            "subject": "mark.index.price",
            "data": {"markPrice": "100.5"}
        }))
        self.assertEqual(pc.get_price("kucoin", "BTC/USDT:USDT"), 100.5)

    def test_kucoin_instrument_funding_rate(self):
        ws, pc, fc = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contract/instrument:XBTUSDTM",
            "subject": "funding.rate",
            "data": {"fundingRate": "0.01", "nextFundingRateTime": "1700000000000"}
        }))
        f = fc.get("BTC/USDT:USDT", "kucoin")
        self.assertIsNotNone(f)
        self.assertEqual(f["funding_rate"], 0.01)

    def test_kucoin_ticker_v2_bid_ask(self):
        ws, pc, _ = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contractMarket/tickerV2:XBTUSDTM",
            "data": {"bestBidPrice": "99.0", "bestAskPrice": "102.0"}
        }))
        bid, ask = pc.get_bid_ask("kucoin", "BTC/USDT:USDT")
        self.assertEqual(bid, 99.0)
        self.assertEqual(ask, 102.0)

    def test_instrument_and_tickerv2_combine(self):
        ws, pc, _ = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contract/instrument:XBTUSDTM",
            "subject": "mark.index.price",
            "data": {"markPrice": "100.5"}
        }))
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contractMarket/tickerV2:XBTUSDTM",
            "data": {"bestBidPrice": "99.0", "bestAskPrice": "102.0"}
        }))
        self.assertEqual(pc.get_price("kucoin", "BTC/USDT:USDT"), 100.5)
        bid, ask = pc.get_bid_ask("kucoin", "BTC/USDT:USDT")
        self.assertEqual(bid, 99.0)
        self.assertEqual(ask, 102.0)

    def test_instrument_does_not_zero_bid(self):
        ws, pc, _ = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contractMarket/tickerV2:XBTUSDTM",
            "data": {"bestBidPrice": "99.0", "bestAskPrice": "102.0"}
        }))
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contract/instrument:XBTUSDTM",
            "subject": "mark.index.price",
            "data": {"markPrice": "100.5"}
        }))
        bid, ask = pc.get_bid_ask("kucoin", "BTC/USDT:USDT")
        self.assertEqual(bid, 99.0)
        self.assertEqual(ask, 102.0)

    def test_old_broken_topic_never_subscribed(self):
        from core.ws_pool import KuCoinWS
        ws = MagicMock()
        with patch("core.ws_pool.KuCoinWS._get_ws_url", return_value="wss://fake/"):
            inst = KuCoinWS(["BTC"], PriceCache(), FundingCache())
        inst._symbols = ["DOT"]
        inst._subscribe_topics(ws)
        all_calls = " ".join(c[0][0] for c in ws.send.call_args_list)
        self.assertIn("/contract/instrument:DOTUSDTM", all_calls)
        self.assertIn("/contractMarket/tickerV2:DOTUSDTM", all_calls)
        self.assertNotIn("/contract/ticker:", all_calls)

    def test_unrecognized_topic_ignored(self):
        ws, pc, _ = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/unknown/foo", "data": {"markPrice": "100"}
        }))
        self.assertEqual(pc.all_symbols(), [])

    def test_welcome_message_ignored(self):
        ws, pc, _ = self._make_kucoin_handler()
        ws._handle_message("kucoin", json.dumps({"type": "welcome"}))
        self.assertEqual(pc.all_symbols(), [])

    def test_granularity_not_confused_with_interval_h(self):
        ws, pc, fc = self._make_kucoin_handler()
        fc.update("kucoin", "BTC/USDT:USDT", 0.01, 0.01, 1700000000000, 8)
        ws._handle_message("kucoin", json.dumps({
            "type": "message", "topic": "/contract/instrument:XBTUSDTM",
            "subject": "funding.rate",
            "data": {"fundingRate": "0.02", "nextFundingRateTime": "1700001000000"}
        }))
        f = fc.get("BTC/USDT:USDT", "kucoin")
        self.assertEqual(f["interval_h"], 8)


class WsPoolBidAskTests(unittest.TestCase):
    def test_bybit_topic_format_correct(self):
        from core.ws_pool import BybitWS
        ws = MagicMock()
        inst = BybitWS(["DOT", "ETH"], PriceCache(), FundingCache())
        inst._subscribe_topics(ws)
        args_sent = json.loads(ws.send.call_args[0][0])
        self.assertEqual(args_sent["op"], "subscribe")
        self.assertIn("tickers.DOTUSDT", args_sent["args"])
        self.assertIn("tickers.ETHUSDT", args_sent["args"])


if __name__ == "__main__":
    unittest.main()
