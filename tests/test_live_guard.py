import os
import unittest
from unittest.mock import Mock, patch

from core.live_engine import LiveEngine, LiveModeLockedError, MissingLiveCredentialsError


def _make_clients(bb_filled=1.0, kc_filled=1.0, bb_avg=100, kc_avg=101):
    bybit = Mock()
    kucoin = Mock()
    bybit.get_usdt_balance.return_value = 1000
    kucoin.get_usdt_balance.return_value = 1000
    bybit.open_market.return_value = {
        "order_id": "bb1", "avg_price": bb_avg, "qty": 1, "requested_qty": 1,
    }
    kucoin.open_market.return_value = {
        "order_id": "kc1", "avg_price": kc_avg, "qty": 1, "requested_qty": 1,
    }
    bybit.get_order_fill.return_value = {
        "status": "filled", "filled_qty": bb_filled, "avg_price": bb_avg,
    }
    kucoin.get_order_fill.return_value = {
        "status": "filled", "filled_qty": kc_filled, "avg_price": kc_avg,
    }
    return bybit, kucoin


class LiveGuardTests(unittest.TestCase):
    def test_live_requires_confirm_true(self):
        with self.assertRaises(LiveModeLockedError):
            LiveEngine(live_confirm=False)

    def test_live_requires_credentials(self):
        with self.assertRaises(MissingLiveCredentialsError):
            LiveEngine(live_confirm=True, bybit_key="", bybit_secret="", kucoin_key="", kucoin_secret="", kucoin_passphrase="")

    @patch("core.live_engine.time.sleep", return_value=None)
    def test_live_engine_can_execute_with_full_fill(self, _mock_sleep):
        bybit, kucoin = _make_clients()
        engine = LiveEngine(live_confirm=True, bybit_client=bybit, kucoin_client=kucoin)
        result = engine.execute_instant("BTC", 100, "sell", "buy", 3)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["position"]["side_bybit"], "sell")
        self.assertEqual(result["position"]["side_kucoin"], "buy")
        self.assertEqual(result["position"]["qty_bybit"], 1.0)
        self.assertEqual(result["position"]["qty_kucoin"], 1.0)

    @patch("core.live_engine.time.sleep", return_value=None)
    def test_unwind_uses_original_side_not_double_flipped(self, _mock_sleep):
        """Regresi bug §1.3: unwind panggil close_market dengan side ASLI."""
        bybit, kucoin = _make_clients()
        bybit.open_market.return_value = {
            "order_id": "bb1", "avg_price": 100, "qty": 1, "requested_qty": 1,
        }
        kucoin.open_market.side_effect = RuntimeError("network timeout")
        engine = LiveEngine(live_confirm=True, bybit_client=bybit, kucoin_client=kucoin)
        result = engine.execute_instant("BTC", 100, "buy", "sell", 3)
        self.assertIn(result["status"], ("failed_unwound", "failed_partial"))
        bybit.close_market.assert_called_once()
        call_args = bybit.close_market.call_args[0]
        # side harus ASLI ("buy"), bukan pre-flip
        self.assertEqual(call_args[1], "buy")

    @patch("core.live_engine.time.sleep", return_value=None)
    def test_partial_fill_triggers_topup_reconciliation(self, _mock_sleep):
        bybit, kucoin = _make_clients(bb_filled=1.0, kc_filled=0.5)
        kucoin.open_market.side_effect = [
            {"order_id": "kc1", "avg_price": 101, "qty": 1, "requested_qty": 1},
            {"order_id": "kc1-top0", "avg_price": 101, "qty": 0.5, "requested_qty": 0.5},
        ]
        kucoin.get_order_fill.side_effect = [
            {"status": "filled", "filled_qty": 0.5, "avg_price": 101},
            {"status": "filled", "filled_qty": 0.5, "avg_price": 101},
        ]
        engine = LiveEngine(live_confirm=True, bybit_client=bybit, kucoin_client=kucoin)
        result = engine.execute_instant("BTC", 100, "sell", "buy", 3)
        self.assertEqual(result["status"], "done")
        self.assertIsNotNone(result["reconciliation"])
        self.assertAlmostEqual(result["position"]["qty_kucoin"], 1.0)
        self.assertGreater(kucoin.open_market.call_count, 1)

    @patch("core.live_engine.time.sleep", return_value=None)
    def test_placement_retries_before_giving_up(self, _mock_sleep):
        bybit, kucoin = _make_clients()
        bybit.open_market.side_effect = [
            RuntimeError("timeout"), RuntimeError("timeout"),
            {"order_id": "bb1", "avg_price": 100, "qty": 1, "requested_qty": 1},
        ]
        engine = LiveEngine(live_confirm=True, bybit_client=bybit, kucoin_client=kucoin)
        result = engine.execute_instant("BTC", 100, "sell", "buy", 3)
        self.assertEqual(result["status"], "done")
        self.assertEqual(bybit.open_market.call_count, 3)

    @patch("core.live_engine.time.sleep", return_value=None)
    def test_zero_fill_leg_gets_unwound(self, _mock_sleep):
        bybit, kucoin = _make_clients(bb_filled=1.0, kc_filled=0.0)
        kucoin.get_order_fill.return_value = {"status": "cancelled", "filled_qty": 0.0, "avg_price": 0}
        engine = LiveEngine(live_confirm=True, bybit_client=bybit, kucoin_client=kucoin)
        result = engine.execute_instant("BTC", 100, "sell", "buy", 3)
        self.assertIn(result["status"], ("failed_unwound", "failed"))
        bybit.close_market.assert_called_once()


if __name__ == "__main__":
    unittest.main()
