"""Automation Engine — restart resume tests for live-mode position monitoring.

Tests resume_live_position() reconstructs a DelayOrder from a persisted
position dict, and that all related state flags are reset correctly.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from core.automation_engine import AutomationEngine, DelayOrder


class ResumeLivePositionTests(unittest.TestCase):
    def setUp(self):
        from core.paper_engine import PaperEngine
        self.paper = PaperEngine()
        self.engine = AutomationEngine(self.paper)

    def _sample_position(self, **overrides):
        """Minimal position dict matching what LiveEngine persists."""
        pos = {
            "id": "abc12345-1234-1234-1234-123456789abc",
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "amount_usd": 100,
            "leverage": 3,
            "entry_spread": -0.05,
            "entry_delta": 0.6,
            "entry_raw_fr_diff": 0.5,
            "entry_rate_bybit": 0.01,
            "entry_rate_kucoin": -0.005,
            "bybit_next_ts": 1700000000,
            "kucoin_next_ts": 1700000100,
            "bybit_interval_h": 8,
            "kucoin_interval_h": 8,
        }
        pos.update(overrides)
        return pos

    def test_resume_sets_state_to_live_and_populates_ids(self):
        self.engine.resume_live_position(self._sample_position())
        self.assertEqual(self.engine._state, self.engine._state.__class__.LIVE)
        self.assertIsNotNone(self.engine._live_position_id)
        self.assertIsInstance(self.engine._live_order, DelayOrder)

    def test_resume_reconstructs_all_delay_order_fields(self):
        pos = self._sample_position()
        self.engine.resume_live_position(pos)

        order = self.engine._live_order
        self.assertEqual(order.symbol, "BTC")
        self.assertEqual(order.side_bybit, "sell")
        self.assertEqual(order.side_kucoin, "buy")
        self.assertEqual(order.amount_usd, 100)
        self.assertEqual(order.leverage, 3)
        self.assertEqual(order.entry_price_spread, -0.05)
        self.assertEqual(order.entry_delta, 0.6)
        self.assertEqual(order.entry_raw_fr_diff, 0.5)
        self.assertEqual(order.bybit_rate, 0.01)
        self.assertEqual(order.kucoin_rate, -0.005)
        self.assertEqual(order.bybit_next_ts, 1700000000)
        self.assertEqual(order.kucoin_next_ts, 1700000100)
        self.assertEqual(order.bybit_interval_h, 8)
        self.assertEqual(order.kucoin_interval_h, 8)

    def test_dominant_payment_ts_picks_higher_abs_rate_exchange(self):
        # Bybit |0.01| > KuCoin |-0.005| → dominant = Bybit next_ts
        pos = self._sample_position(entry_rate_bybit=0.01, entry_rate_kucoin=-0.005,
                                     bybit_next_ts=1700000000, kucoin_next_ts=1700000100)
        self.engine.resume_live_position(pos)
        self.assertEqual(self.engine._live_order.dominant_payment_ts, 1700000000)

        # KuCoin |-0.02| > Bybit |0.01| → dominant = KuCoin next_ts
        pos2 = self._sample_position(entry_rate_bybit=0.01, entry_rate_kucoin=-0.02,
                                      bybit_next_ts=1700000000, kucoin_next_ts=1700000200)
        self.engine.resume_live_position(pos2)
        self.assertEqual(self.engine._live_order.dominant_payment_ts, 1700000200)

    def test_stale_flags_cleared_on_resume(self):
        self.engine._hedge_triggered = True
        self.engine._funding_threshold_met = True

        self.engine.resume_live_position(self._sample_position())
        self.assertFalse(self.engine._hedge_triggered)
        self.assertFalse(self.engine._funding_threshold_met)

    def test_hedge_check_reset_to_zero_for_immediate_tick(self):
        self.engine._last_hedge_check = 999999.0
        self.engine.resume_live_position(self._sample_position())
        self.assertEqual(self.engine._last_hedge_check, 0.0)

    def test_missing_optional_fields_resume_without_raising(self):
        # Position saved by older bot version (before this fix)
        old_pos = {
            "id": "old-pos-123",
            "symbol": "ETH",
            "side_bybit": "buy",
            "side_kucoin": "sell",
            "amount_usd": 50,
            "leverage": 2,
        }
        # Must not raise
        self.engine.resume_live_position(old_pos)
        self.assertEqual(self.engine._state, self.engine._state.__class__.LIVE)
        self.assertIsNotNone(self.engine._live_order)
        # Defaults
        self.assertEqual(self.engine._live_order.entry_delta, 0)
        self.assertEqual(self.engine._live_order.entry_price_spread, 0)


if __name__ == "__main__":
    unittest.main()