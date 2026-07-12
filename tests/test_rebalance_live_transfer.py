import os
import json
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.rebalance_engine import RebalanceEngine, RebalanceStatus


class LiveTransferTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._log_path = os.path.join(self._tmpdir.name, "rebalance_transfers.jsonl")
        self._patcher = patch("core.rebalance_engine.TRANSFER_LOG_FILE", self._log_path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

        self.mock_engine = MagicMock()
        self.mock_engine.bybit = MagicMock()
        self.mock_engine.kucoin = MagicMock()

    def _make(self):
        return RebalanceEngine(self.mock_engine, paper_mode=False)

    def _status(self, amount):
        return RebalanceStatus(100, 100, 200, 0.5, 0.5, False, True, "bybit", "kucoin", amount, 0.4)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", False)
    def test_disabled_flag_never_calls_withdraw(self):
        eng = self._make()
        eng.start_rebalance(self._status(50))
        self.mock_engine.bybit.withdraw.assert_not_called()
        self.assertFalse(eng._is_rebalancing)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", True)
    @patch("core.rebalance_engine.REBALANCE_LIVE_DRY_RUN", True)
    @patch("core.rebalance_engine.REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "x")
    def test_dry_run_never_calls_withdraw(self):
        eng = self._make()
        eng.start_rebalance(self._status(50))
        self.mock_engine.bybit.withdraw.assert_not_called()
        self.assertFalse(eng._is_rebalancing)
        with open(self._log_path) as f:
            log_str = f.read()
        self.assertIn("withdraw_initiated", log_str)
        self.assertIn('"dry_run": true', log_str)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", True)
    @patch("core.rebalance_engine.REBALANCE_LIVE_DRY_RUN", False)
    @patch("core.rebalance_engine.REBALANCE_MIN_TRANSFER_USD", 50)
    @patch("core.rebalance_engine.REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "x")
    def test_guard_rejects_amount_below_min(self):
        eng = self._make()
        eng.start_rebalance(self._status(49))
        self.mock_engine.bybit.withdraw.assert_not_called()
        self.assertFalse(eng._is_rebalancing)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", True)
    @patch("core.rebalance_engine.REBALANCE_LIVE_DRY_RUN", False)
    @patch("core.rebalance_engine.REBALANCE_MAX_TRANSFER_USD", 500)
    @patch("core.rebalance_engine.REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "x")
    def test_guard_rejects_amount_above_max(self):
        eng = self._make()
        eng.start_rebalance(self._status(501))
        self.mock_engine.bybit.withdraw.assert_not_called()
        self.assertFalse(eng._is_rebalancing)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", True)
    @patch("core.rebalance_engine.REBALANCE_LIVE_DRY_RUN", False)
    @patch("core.rebalance_engine.REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "")
    def test_guard_rejects_missing_address(self):
        eng = self._make()
        eng.start_rebalance(self._status(50))
        self.mock_engine.bybit.withdraw.assert_not_called()
        self.assertFalse(eng._is_rebalancing)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", True)
    @patch("core.rebalance_engine.REBALANCE_LIVE_DRY_RUN", False)
    @patch("core.rebalance_engine.REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "x")
    def test_withdraw_called_with_unique_client_id_each_cycle(self):
        self.mock_engine.bybit.withdraw.return_value = {"withdraw_id": "w1"}
        eng1 = self._make()
        eng1.start_rebalance(self._status(50))
        
        self.mock_engine.bybit.withdraw.return_value = {"withdraw_id": "w2"}
        eng2 = self._make()
        eng2.start_rebalance(self._status(50))

        calls = self.mock_engine.bybit.withdraw.call_args_list
        self.assertEqual(len(calls), 2)
        cid1 = calls[0].kwargs.get("client_id")
        cid2 = calls[1].kwargs.get("client_id")
        self.assertIsNotNone(cid1)
        self.assertIsNotNone(cid2)
        self.assertNotEqual(cid1, cid2)

    def _setup_inflight_withdraw(self):
        eng = self._make()
        eng._is_rebalancing = True
        record = {"from": "bybit", "to": "kucoin", "token": "USDT", "amount": 50, "address": "x", "ts": time.time(), "client_id": "c1"}
        eng._live_withdraw_poll = {
            "client": self.mock_engine.bybit,
            "withdraw_id": "w1",
            "deadline": time.time() + 1000,
            "record": record,
        }
        return eng

    @patch("core.rebalance_engine.REBALANCE_WITHDRAW_POLL_INTERVAL_SEC", 0)
    def test_tick_polls_withdrawal_status_not_balance_when_inflight(self):
        eng = self._setup_inflight_withdraw()
        self.mock_engine.bybit.get_withdrawal_status.return_value = {"status": "processing"}
        res = eng.tick(time.time() + 1)
        self.assertEqual(res, "waiting")
        self.mock_engine.bybit.get_withdrawal_status.assert_called_with("w1")

    @patch("core.rebalance_engine.REBALANCE_WITHDRAW_POLL_INTERVAL_SEC", 0)
    def test_tick_marks_done_on_complete_status(self):
        eng = self._setup_inflight_withdraw()
        self.mock_engine.bybit.get_withdrawal_status.return_value = {"status": "complete"}
        res = eng.tick(time.time() + 1)
        self.assertEqual(res, "waiting")  # moves to deposit phase, still waiting
        self.assertIsNone(eng._live_withdraw_poll)
        self.assertIsNotNone(eng._live_deposit_poll)

    @patch("core.rebalance_engine.REBALANCE_WITHDRAW_POLL_INTERVAL_SEC", 0)
    def test_tick_marks_failed_on_failed_status_and_stops_polling(self):
        eng = self._setup_inflight_withdraw()
        self.mock_engine.bybit.get_withdrawal_status.return_value = {"status": "failed"}
        res = eng.tick(time.time() + 1)
        self.assertEqual(res, "failed")
        self.assertIsNone(eng._live_withdraw_poll)
        self.assertFalse(eng._is_rebalancing)

    @patch("core.rebalance_engine.REBALANCE_LIVE_TRANSFER_ENABLED", True)
    @patch("core.rebalance_engine.REBALANCE_LIVE_DRY_RUN", False)
    @patch("core.rebalance_engine.REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "x")
    def test_transfer_log_written_before_api_call(self):
        def crash_withdraw(*a, **kw):
            raise RuntimeError("api crashed")
        self.mock_engine.bybit.withdraw.side_effect = crash_withdraw
        eng = self._make()
        eng.start_rebalance(self._status(50))
        
        with open(self._log_path) as f:
            logs = [json.loads(l) for l in f]
            
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["type"], "withdraw_initiated")
        self.assertEqual(logs[1]["type"], "withdraw_call_failed")


if __name__ == "__main__":
    unittest.main()