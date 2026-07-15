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
        # bb=0.4, kc=1.0 expected=1.0 each → drift=|0.4-1.0|=0.6
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
        # Live size larger than expected → clamped to 1.0, drift=0
        drift = _hedge_leg_drift(2.0, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(drift, 0.0)


class HedgeGuardIntegrationTests(unittest.TestCase):
    """Integration: AutomationEngine._tick_live() hedge branch."""

    def setUp(self):
        from core.automation_engine import AutomationEngine
        from core.paper_engine import PaperEngine
        self.paper = PaperEngine()
        self.engine = AutomationEngine(self.paper)

    def _make_live_pos(self):
        """Create a mock live position with known sizes."""
        return {
            "id": "test-live-pos-1",
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
            "bybit_interval_h": 8,
            "kucoin_interval_h": 8,
            "entry_time": "2026-01-01T00:00:00+00:00",
            "amount_usd": 100,
            "position_size": 300,
            "leverage": 3,
            "status": "open",
            "paper": False,
        }

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    @patch("core.automation_engine.HEDGE_BALANCE_DROP_THRESHOLD", 0.95)
    def test_partial_liquidation_triggers_emergency(self):
        live_eng = MagicMock()
        self.engine._live_engine = live_eng
        self.engine._live_position_id = "test-live-pos-1"
        self.engine._state = self.engine.state  # keep current
        self.engine._last_hedge_check = 0.0
        self.engine._hedge_triggered = False

        pos = self._make_live_pos()
        self.paper._positions = [pos]

        # Both legs "open" but sizes are mismatched — partial liquidation
        live_eng.get_position_status.return_value = {
            "bybit": "open", "kucoin": "open",
            "bybit_size": 0.4, "kucoin_size": 1.0,
        }
        live_eng.close_position.return_value = {"ok": True, "realized_pnl": -5.0}
        live_eng.get_ticker.return_value = {"mark_price": 100}
        live_eng.get_usdt_balance.return_value = 1000

        self.engine._tick_live(time.time())
        self.assertEqual(self.engine._live_position_id, None)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    @patch("core.automation_engine.HEDGE_BALANCE_DROP_THRESHOLD", 0.95)
    def test_small_drift_does_not_trigger(self):
        live_eng = MagicMock()
        self.engine._live_engine = live_eng
        self.engine._live_position_id = "test-live-pos-1"
        self.engine._last_hedge_check = 0.0
        self.engine._hedge_triggered = False

        pos = self._make_live_pos()
        self.paper._positions = [pos]

        live_eng.get_position_status.return_value = {
            "bybit": "open", "kucoin": "open",
            "bybit_size": 0.99, "kucoin_size": 1.0,
        }

        self.engine._tick_live(time.time())
        self.assertFalse(self.engine._hedge_triggered)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    def test_full_leg_loss_still_works(self):
        """Regression: full-leg-loss detection still triggers (unchanged path)."""
        live_eng = MagicMock()
        self.engine._live_engine = live_eng
        self.engine._live_position_id = "test-live-pos-1"
        self.engine._last_hedge_check = 0.0
        self.engine._hedge_triggered = False

        pos = self._make_live_pos()
        self.paper._positions = [pos]

        live_eng.get_position_status.return_value = {
            "bybit": "closed", "kucoin": "open",
            "bybit_size": 0.0, "kucoin_size": 1.0,
        }
        live_eng.close_position.return_value = {"ok": True, "realized_pnl": -5.0}
        live_eng.get_ticker.return_value = {"mark_price": 100}
        live_eng.get_usdt_balance.return_value = 1000

        self.engine._tick_live(time.time())
        self.assertEqual(self.engine._live_position_id, None)

    @patch("core.automation_engine.PAPER_MODE", False)
    @patch("core.automation_engine.HEDGE_EMERGENCY_OPEN", True)
    @patch("core.automation_engine.HEDGE_CHECK_INTERVAL_SEC", 0)
    def test_api_error_does_not_crash(self):
        live_eng = MagicMock()
        self.engine._live_engine = live_eng
        self.engine._live_position_id = "test-live-pos-1"
        self.engine._last_hedge_check = 0.0
        self.engine._hedge_triggered = False

        pos = self._make_live_pos()
        self.paper._positions = [pos]

        live_eng.get_position_status.side_effect = RuntimeError("API down")

        # Must not raise
        self.engine._tick_live(time.time())
        self.assertFalse(self.engine._hedge_triggered)


if __name__ == "__main__":
    unittest.main()