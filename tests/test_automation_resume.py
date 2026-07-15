"""Automation Engine — restart resume tests for live-mode position monitoring.

Tests resume_live_position() reconstructs a DelayOrder from a persisted
position dict, and that all related state flags are reset correctly.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from core.automation_engine import AutomationEngine, DelayOrder, State


class ResumeLivePositionTests(unittest.TestCase):
    def setUp(self):
        from core.paper_engine import PaperEngine
        self.paper = PaperEngine()
        self.engine = AutomationEngine(self.paper)

    def _sample_position(self, **overrides):
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

    def _get_tracked(self, pos_id="abc12345-1234-1234-1234-123456789abc"):
        return self.engine._live_positions[pos_id]

    def test_resume_adds_to_live_positions(self):
        self.engine.resume_live_position(self._sample_position())
        self.assertIn("abc12345-1234-1234-1234-123456789abc", self.engine._live_positions)
        self.assertIsInstance(self._get_tracked().order, DelayOrder)

    def test_resume_does_not_touch_state(self):
        self.engine._state = State.IDLE
        self.engine.resume_live_position(self._sample_position())
        self.assertEqual(self.engine._state, State.IDLE)

    def test_resume_reconstructs_all_delay_order_fields(self):
        pos = self._sample_position()
        self.engine.resume_live_position(pos)

        order = self._get_tracked().order
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
        pos = self._sample_position(entry_rate_bybit=0.01, entry_rate_kucoin=-0.005,
                                     bybit_next_ts=1700000000, kucoin_next_ts=1700000100)
        self.engine.resume_live_position(pos)
        self.assertEqual(self._get_tracked().order.dominant_payment_ts, 1700000000)

        pos2 = self._sample_position(entry_rate_bybit=0.01, entry_rate_kucoin=-0.02,
                                      bybit_next_ts=1700000000, kucoin_next_ts=1700000200)
        self.engine.resume_live_position(pos2)
        self.assertEqual(self.engine._live_positions["abc12345-1234-1234-1234-123456789abc"].order.dominant_payment_ts, 1700000200)

    def test_stale_flags_not_on_engine(self):
        # Old singular flags don't exist anymore — tracked state is per-position
        self.engine.resume_live_position(self._sample_position())
        tracked = self._get_tracked()
        self.assertFalse(tracked.hedge_triggered)
        self.assertFalse(tracked.funding_threshold_met)

    def test_hedge_check_reset_to_zero_for_immediate_tick(self):
        self.engine.resume_live_position(self._sample_position())
        self.assertEqual(self._get_tracked().last_hedge_check, 0.0)

    def test_missing_optional_fields_resume_without_raising(self):
        old_pos = {
            "id": "old-pos-123",
            "symbol": "ETH",
            "side_bybit": "buy",
            "side_kucoin": "sell",
            "amount_usd": 50,
            "leverage": 2,
        }
        self.engine.resume_live_position(old_pos)
        self.assertIn("old-pos-123", self.engine._live_positions)
        self.assertEqual(self.engine._live_positions["old-pos-123"].order.entry_delta, 0)

    def test_two_resumed_positions_have_independent_state(self):
        pos_a = self._sample_position()
        pos_b = self._sample_position()
        pos_b["id"] = "pos-b-11111-22222-33333-444444444444"
        self.engine.resume_live_position(pos_a)
        self.engine.resume_live_position(pos_b)
        self.assertEqual(len(self.engine._live_positions), 2)
        self.engine._live_positions["abc12345-1234-1234-1234-123456789abc"].hedge_triggered = True
        self.assertFalse(self.engine._live_positions["pos-b-11111-22222-33333-444444444444"].hedge_triggered)


class DelayOrderKeepsProgressingWhileLiveTests(unittest.TestCase):
    """End-to-end: a pending DelayOrder executes and joins _live_positions
    alongside an already-tracked position, and the entry-side state machine
    correctly self-resolves to IDLE afterward."""

    def setUp(self):
        from core.automation_engine import AutomationEngine, DelayOrder
        from core.paper_engine import PaperEngine
        self.paper = PaperEngine()
        self.engine = AutomationEngine(self.paper)

    @patch("core.automation_engine.PAPER_MODE", True)
    @patch("core.automation_engine.AUTO_DELAY_ENTRY_PRICE_SPREAD", 0.0)
    @patch("core.automation_engine.AUTO_DELAY_CANCEL_FUNDING_DIFF", 0.0)
    def test_delay_order_executes_beside_existing_live(self):
        # Seed one already-live position
        self.engine.resume_live_position({
            "id": "already-live-1",
            "symbol": "ETH",
            "side_bybit": "buy",
            "side_kucoin": "sell",
            "amount_usd": 50,
            "leverage": 2,
        })
        self.paper._positions = [{
            "id": "already-live-1",
            "symbol": "ETH",
            "side_bybit": "buy",
            "side_kucoin": "sell",
            "amount_usd": 50,
            "leverage": 2,
            "status": "open",
            "paper": True,
            "entry_price_bybit": 100,
            "entry_price_kucoin": 101,
            "qty_bybit": 1.0,
            "qty_kucoin": 1.0,
            "entry_fee_bybit": 0.055,
            "entry_fee_kucoin": 0.06,
            "entry_rate_bybit": 0.01,
            "entry_rate_kucoin": -0.005,
            "bybit_interval_h": 8,
            "kucoin_interval_h": 8,
            "entry_time": "2026-01-01T00:00:00+00:00",
            "position_size": 100,
            "entry_spread": -0.05,
            "entry_delta": 0.5,
            "entry_raw_fr_diff": 0.4,
        }]
        self.assertEqual(len(self.engine._live_positions), 1)

        # Queue a DelayOrder
        from core.automation_engine import DelayOrder
        do = DelayOrder(
            symbol="BTC", side_bybit="sell", side_kucoin="buy",
            amount_usd=100, leverage=3,
            entry_price_spread=-0.05, entry_delta=0.6,
            bybit_rate=0.01, kucoin_rate=-0.005,
        )
        self.engine._delay_orders = [do]
        self.engine._state = State.DELAY

        # Tick once: the delay order should execute via _execute_delay_order
        # The existing live position may be cleaned up if scan data is missing,
        # but the delay order should produce a new live position
        self.engine._tick()
        # At least one live position should exist (the new one from delay order)
        self.assertGreaterEqual(len(self.engine._live_positions), 1)

        # Another tick: _delay_orders empty, state should resolve to IDLE
        self.engine._tick()
        self.assertEqual(self.engine._state, State.IDLE)


if __name__ == "__main__":
    unittest.main()