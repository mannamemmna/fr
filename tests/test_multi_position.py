"""Multi-position live tracking — integration tests.

Proves the engine can track multiple concurrently-open positions and that
the entry-side state machine can still find new candidates while positions
are live (AUTO_MAX_POSITIONS>1).
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from core.automation_engine import AutomationEngine, State


class MultiPositionTests(unittest.TestCase):
    def setUp(self):
        from core.paper_engine import PaperEngine
        self.paper = PaperEngine()
        self.engine = AutomationEngine(self.paper)

    def test_disable_enable_preserves_live_positions(self):
        self.engine.resume_live_position({
            "id": "pos-1",
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "amount_usd": 100,
            "leverage": 3,
        })
        self.assertEqual(len(self.engine._live_positions), 1)

        self.engine.disable()
        self.assertEqual(len(self.engine._live_positions), 1)

        self.engine.enable()
        self.assertEqual(len(self.engine._live_positions), 1)

    def test_get_status_reports_live_positions(self):
        self.engine.resume_live_position({
            "id": "aaaa1111-2222-3333-4444-555555555555",
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "amount_usd": 100,
            "leverage": 3,
        })
        self.engine.resume_live_position({
            "id": "bbbb1111-2222-3333-4444-555555555555",
            "symbol": "ETH",
            "side_bybit": "buy",
            "side_kucoin": "sell",
            "amount_usd": 50,
            "leverage": 2,
        })

        status = self.engine.get_status()
        self.assertIn("live_positions", status)
        self.assertEqual(len(status["live_positions"]), 2)

    def test_get_status_omits_live_positions_when_empty(self):
        status = self.engine.get_status()
        self.assertNotIn("live_positions", status)

    @patch("core.automation_engine.AUTO_MAX_POSITIONS", 2)
    @patch("core.automation_engine.AUTO_ENTRY_WINDOW_MIN", 30)
    @patch("core.automation_engine.AUTO_DELTA_THRESHOLD", 0.0)
    def test_looking_can_find_second_candidate_while_live(self):
        """_tick_looking is now reachable while positions are live."""
        # Seed one live position
        self.engine.resume_live_position({
            "id": "pos-1",
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "amount_usd": 100,
            "leverage": 3,
        })
        self.paper._positions = [{
            "id": "pos-1",
            "symbol": "BTC",
            "side_bybit": "sell",
            "side_kucoin": "buy",
            "amount_usd": 100,
            "leverage": 3,
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
            "position_size": 300,
            "entry_spread": -0.05,
            "entry_delta": 0.5,
            "entry_raw_fr_diff": 0.4,
        }]

        # Set up scan with two candidates
        now = time.time()
        scan = [
            {"symbol": "ETH", "delta_pct": 0.8, "bybit_rate_pct": 0.02, "kucoin_rate_pct": -0.015,
             "bybit_next_ts": now + 600, "kucoin_next_ts": now + 600,
             "bybit_interval_h": 8, "kucoin_interval_h": 8, "bybit_action": "SHORT", "kucoin_action": "LONG",
             "direction": "SHORT-BB / LONG-KC", "spread_pct": -0.1, "raw_fr_diff": 3.5},
            {"symbol": "SOL", "delta_pct": 0.6, "bybit_rate_pct": 0.015, "kucoin_rate_pct": -0.01,
             "bybit_next_ts": now + 600, "kucoin_next_ts": now + 600,
             "bybit_interval_h": 8, "kucoin_interval_h": 8, "bybit_action": "SHORT", "kucoin_action": "LONG",
             "direction": "SHORT-BB / LONG-KC", "spread_pct": -0.05, "raw_fr_diff": 2.5},
        ]
        self.engine._get_scan = lambda: scan
        self.engine._state = State.IDLE

        # Tick: should go IDLE → LOOKING. With AUTO_MAX_POSITIONS=2,
        # _tick_looking can now be reached while positions are live.
        self.engine._tick()
        # State should have moved from IDLE (either LOOKING or DELAY)
        self.assertIn(self.engine._state, (State.LOOKING, State.DELAY))

    def test_state_enum_has_no_live(self):
        """State.LIVE is fully retired."""
        self.assertFalse(hasattr(State, "LIVE"))


if __name__ == "__main__":
    unittest.main()