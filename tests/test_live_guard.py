import os
import unittest
from unittest.mock import Mock, patch

from core.live_engine import LiveEngine, LiveModeLockedError, MissingLiveCredentialsError


class LiveGuardTests(unittest.TestCase):
    def test_live_requires_confirm_true(self):
        with self.assertRaises(LiveModeLockedError):
            LiveEngine(live_confirm=False)

    def test_live_requires_credentials(self):
        with self.assertRaises(MissingLiveCredentialsError):
            LiveEngine(live_confirm=True, bybit_key="", bybit_secret="", kucoin_key="", kucoin_secret="", kucoin_passphrase="")

    def test_live_engine_can_execute_with_mock_clients(self):
        bybit = Mock()
        kucoin = Mock()
        bybit.get_usdt_balance.return_value = 1000
        kucoin.get_usdt_balance.return_value = 1000
        bybit.open_market.return_value = {"order_id": "bb1", "avg_price": 100, "qty": 1}
        kucoin.open_market.return_value = {"order_id": "kc1", "avg_price": 101, "qty": 1}
        engine = LiveEngine(live_confirm=True, bybit_client=bybit, kucoin_client=kucoin)
        result = engine.execute_instant("BTC", 100, "sell", "buy", 3)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["position"]["side_bybit"], "sell")
        self.assertEqual(result["position"]["side_kucoin"], "buy")
        bybit.open_market.assert_called_once()
        kucoin.open_market.assert_called_once()


if __name__ == "__main__":
    unittest.main()
