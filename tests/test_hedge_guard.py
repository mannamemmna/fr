"""Hedge Integrity Guard — partial-leg-liquidation detection tests.

Tests for _hedge_leg_drift() pure function + AutomationEngine hedge check
integration with partial liquidation detection via HEDGE_BALANCE_DROP_THRESHOLD.
"""

import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from core.automation_engine import _hedge_leg_drift


class DriftPureFunctionTests(unittest.TestCase):
    """Test _hedge_leg_drift() in isolation — no exchange or engine needed."""

    def test_equal_legs_zero_drift(self):
        self.assertAlmostEqual(_hedge_leg_drift(1.0, 1.0, 1.0, 1.0), 0.0)

    def test_full_loss_one_leg_max_drift(self):
        self.assertAlmostEqual(_hedge_leg_drift(1.0, 0.0, 1.0, 1.0), 1.0)

    def test_partial_liquidation_40_percent(self):
        drift = _hedge_leg_drift(0.4, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(drift, 0.6)

    def test_small_noise_does_not_false_positive(self):
        drift = _hedge_leg_drift(0.99, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(drift, 0.01)

    def test_missing_expected_size_returns_none(self):
        self.assertIsNone(_hedge_leg_drift(1.0, 1.0, 0, 1.0))
        self.assertIsNone(_hedge_leg_drift(1.0, 1.0, 1.0, 0))
        self.assertIsNone(_hedge_leg_drift(1.0, 1.0, -1, 1.0))

    def test_size_above_expected_clamped(self):
        drift = _hedge_leg_drift(2.0, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(drift, 0.0)


class HedgeGuardIntegrationTests(unittest.TestCase):
    """Integration: AutomationEngine hedge check via _tick_live_positions."""

    def setUp(self):
        from core.automation_engine import AutomationEngine
        from core.paper_engine import PaperEngine
        self.paper = PaperEngine()
        self.engine = AutomationEngine(self.paper)

    def _make_live_pos(self, pos_id="test-live-pos-1", **overrides):
        pos = {
            "id": pos_id,
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "qty_bybit": 1.0,
            "qty_kucoin": 1.0,
            "entry_price_bybit": 100.0,
            "entry_price_kucoin": 101.0,
            "entry_fee_bybit": 0.055,
            "entry_fee_kucoin": 0.06,
            "entry_rate_bybit": 0.01,
            "entry_rate_kucoin": -0.005,
            "bybit_next_ts": 1700000000,
            "kucoin_next_ts": 1700000100,
            "bybit_interval_h": 8,
            "kucoin_interval_h": 8,
            "entry_time": "2026-01-01T00:00:00+00:00",
            "amount_usd": 100,
            "position_size": 300,
            "leverage": 3,
            "entry_spread": -0.05,
            "entry_delta": 0.6,
            "entry_raw_fr_diff": 0.5,
            "status": "open",
            "paper": False,
        }
        pos.update(overrides)
        return pos

    def _seed_position(self, pos_id="test-live-pos-1"):
        """Seed a position via resume_live_position (public API)."""
        pos = self._make_live_pos(pos_id=pos_id)
        self.paper._positions = [pos]
        self.engine.resume_live_position(pos)
        return self.engine._live_positions[pos_id]

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    @patch("core.automation_engine.HEDGE_BALANCE_DROP_THRESHOLD", 0.95)
    def test_partial_liquidation_triggers_emergency(self):
        live_eng = MagicMock()
        self.engine._live_engine = live_eng

        tracked = self._seed_position()
        live_eng.get_position_status.return_value = {
            "bybit": "open", "kucoin": "open",
            "bybit_size": 0.4, "kucoin_size": 1.0,
        }
        live_eng.close_position.return_value = {"ok": True, "realized_pnl": -5.0}
        live_eng.get_ticker.return_value = {"mark_price": 100}
        live_eng.get_usdt_balance.return_value = 1000

        self.engine._tick_live_positions(time.time())
        self.assertNotIn("test-live-pos-1", self.engine._live_positions)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    @patch("core.automation_engine.HEDGE_BALANCE_DROP_THRESHOLD", 0.95)
    def test_small_drift_does_not_trigger(self):
        live_eng = MagicMock()
        self.engine._live_engine = live_eng

        tracked = self._seed_position()
        live_eng.get_position_status.return_value = {
            "bybit": "open", "kucoin": "open",
            "bybit_size": 0.99, "kucoin_size": 1.0,
        }

        self.engine._tick_live_positions(time.time())
        self.assertIn("test-live-pos-1", self.engine._live_positions)
        self.assertFalse(self.engine._live_positions["test-live-pos-1"].hedge_triggered)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    def test_full_leg_loss_still_works(self):
        """Regression: full-leg-loss detection still triggers."""
        live_eng = MagicMock()
        self.engine._live_engine = live_eng

        tracked = self._seed_position()
        live_eng.get_position_status.return_value = {
            "bybit": "closed", "kucoin": "open",
            "bybit_size": 0.0, "kucoin_size": 1.0,
        }
        live_eng.close_position.return_value = {"ok": True, "realized_pnl": -5.0}
        live_eng.get_ticker.return_value = {"mark_price": 100}
        live_eng.get_usdt_balance.return_value = 1000

        self.engine._tick_live_positions(time.time())
        self.assertNotIn("test-live-pos-1", self.engine._live_positions)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    def test_api_error_does_not_crash(self):
        live_eng = MagicMock()
        self.engine._live_engine = live_eng

        tracked = self._seed_position()
        live_eng.get_position_status.side_effect = RuntimeError("API down")

        self.engine._tick_live_positions(time.time())
        self.assertIn("test-live-pos-1", self.engine._live_positions)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    @patch("core.automation_engine.HEDGE_BALANCE_DROP_THRESHOLD", 0.95)
    def test_emergency_on_one_does_not_disturb_other(self):
        """Emergency close on position A doesn't touch position B."""
        live_eng = MagicMock()
        self.engine._live_engine = live_eng

        tracked_a = self._seed_position(pos_id="pos-a")
        tracked_b = self._seed_position(pos_id="pos-b")

        # Position A: partial liquidation (triggers emergency)
        # Position B: healthy
        live_eng.get_position_status.side_effect = [
            {"bybit": "open", "kucoin": "open", "bybit_size": 0.4, "kucoin_size": 1.0},
            {"bybit": "open", "kucoin": "open", "bybit_size": 1.0, "kucoin_size": 1.0},
        ]
        live_eng.close_position.return_value = {"ok": True, "realized_pnl": -5.0}
        live_eng.get_ticker.return_value = {"mark_price": 100}
        live_eng.get_usdt_balance.return_value = 1000

        self.engine._tick_live_positions(time.time())
        self.assertNotIn("test-live-pos-1", self.engine._live_positions)


if __name__ == "__main__":
    unittest.main()