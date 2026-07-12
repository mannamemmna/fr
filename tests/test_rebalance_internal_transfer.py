import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.rebalance_engine import RebalanceEngine


class InternalTransferTests(unittest.TestCase):
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

    def _setup_deposit_poll(self, to_ex="kucoin", since_ts=1000):
        eng = self._make()
        eng._is_rebalancing = True
        record = {"from": "bybit" if to_ex=="kucoin" else "kucoin", "to": to_ex, 
                  "token": "USDT", "amount": 50, "address": "x", "ts": since_ts, "client_id": "c1"}
        client = self.mock_engine.kucoin if to_ex == "kucoin" else self.mock_engine.bybit
        eng._live_deposit_poll = {
            "client": client,
            "to_exchange": to_ex,
            "coin": "USDT",
            "amount": 50,
            "address": "x",
            "since_ts": since_ts,
            "deadline": time.time() + 1000,
            "record": record,
        }
        return eng

    @patch("core.rebalance_engine.REBALANCE_DEPOSIT_POLL_INTERVAL_SEC", 0)
    def test_deposit_matcher_finds_matching_row_within_tolerance(self):
        eng = self._setup_deposit_poll()
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.return_value = {"id": "d1", "amount": 50}
        
        # Action
        res = eng.tick(time.time() + 1)
        
        self.assertEqual(res, "waiting")
        self.assertIsNone(eng._live_deposit_poll)
        self.assertIsNotNone(eng._live_internal_transfer_poll)

    def test_deposit_matcher_ignores_rows_before_since_ts(self):
        # Implementation is in the exchange client, but we verify it's passed correctly
        eng = self._setup_deposit_poll(since_ts=1000)
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.return_value = None
        
        res = eng.tick(time.time() + 100)
        
        self.assertEqual(res, "waiting")
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.assert_called_with("USDT", 50, "x", 1000)
        self.assertIsNotNone(eng._live_deposit_poll)

    def test_deposit_matcher_ignores_wrong_address(self):
        # Implementation is in the exchange client, but we verify it's passed correctly
        eng = self._setup_deposit_poll()
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.return_value = None
        
        res = eng.tick(time.time() + 100)
        
        self.assertEqual(res, "waiting")
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.assert_called_with("USDT", 50, "x", 1000)

    @patch("core.rebalance_engine.REBALANCE_WITHDRAW_POLL_INTERVAL_SEC", 0)
    def test_on_withdraw_complete_starts_deposit_poll(self):
        eng = self._make()
        eng._is_rebalancing = True
        record = {"from": "bybit", "to": "kucoin", "token": "USDT", "amount": 50, "address": "x", "ts": 1000, "client_id": "c1"}
        eng._live_withdraw_poll = {
            "client": self.mock_engine.bybit,
            "withdraw_id": "w1",
            "deadline": time.time() + 1000,
            "record": record,
        }
        self.mock_engine.bybit.get_withdrawal_status.return_value = {"status": "complete"}
        
        eng.tick(time.time() + 1)
        
        self.assertIsNone(eng._live_withdraw_poll)
        self.assertIsNotNone(eng._live_deposit_poll)
        self.assertEqual(eng._live_deposit_poll["to_exchange"], "kucoin")
        
        with open(self._log_path) as f:
            log_str = f.read()
        self.assertIn("withdraw_complete_awaiting_deposit", log_str)

    @patch("core.rebalance_engine.REBALANCE_DEPOSIT_POLL_INTERVAL_SEC", 0)
    def test_on_deposit_confirmed_calls_correct_internal_transfer_per_exchange(self):
        # Test KuCoin path
        eng_kc = self._setup_deposit_poll(to_ex="kucoin")
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.return_value = {"id": "d1"}
        self.mock_engine.kucoin.transfer_main_to_futures.return_value = {"transfer_id": "t1"}
        
        eng_kc.tick(time.time() + 1)
        
        self.mock_engine.kucoin.transfer_main_to_futures.assert_called_once()
        self.assertTrue(eng_kc._live_internal_transfer_poll["assume_complete"])
        
        # Test Bybit path
        eng_bb = self._setup_deposit_poll(to_ex="bybit")
        self.mock_engine.bybit.find_deposit_by_amount_and_address.return_value = {"id": "d1"}
        self.mock_engine.bybit.transfer_funding_to_unified.return_value = {"transfer_id": "t1"}
        
        eng_bb.tick(time.time() + 1)
        
        self.mock_engine.bybit.transfer_funding_to_unified.assert_called_once()
        self.assertFalse(eng_bb._live_internal_transfer_poll["assume_complete"])

    @patch("core.rebalance_engine.REBALANCE_DEPOSIT_POLL_INTERVAL_SEC", 0)
    def test_internal_transfer_call_failure_releases_rebalancing_lock_not_stuck_forever(self):
        eng = self._setup_deposit_poll()
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.return_value = {"id": "d1"}
        self.mock_engine.kucoin.transfer_main_to_futures.side_effect = RuntimeError("api err")
        
        eng.tick(time.time() + 1)
        
        self.assertFalse(eng._is_rebalancing)  # Must release lock so manual retry is possible
        self.assertIsNone(eng._live_deposit_poll)
        
        with open(self._log_path) as f:
            log_str = f.read()
        self.assertIn("internal_transfer_call_failed", log_str)

    @patch("core.rebalance_engine.REBALANCE_DEPOSIT_POLL_INTERVAL_SEC", 0)
    @patch("core.rebalance_engine.REBALANCE_INTERNAL_TRANSFER_POLL_INTERVAL_SEC", 0)
    def test_kucoin_internal_transfer_treated_as_synchronous_complete(self):
        eng = self._setup_deposit_poll(to_ex="kucoin")
        self.mock_engine.kucoin.find_deposit_by_amount_and_address.return_value = {"id": "d1"}
        self.mock_engine.kucoin.transfer_main_to_futures.return_value = {"transfer_id": "t1"}
        
        # Tick 1: detects deposit, calls transfer_main_to_futures
        eng.tick(time.time() + 1)
        self.assertIsNotNone(eng._live_internal_transfer_poll)
        self.assertTrue(eng._live_internal_transfer_poll["assume_complete"])
        
        # Tick 2: handles the synchronous completion
        res = eng.tick(time.time() + 2)
        
        self.assertEqual(res, "done")
        self.assertFalse(eng._is_rebalancing)
        self.assertIsNone(eng._live_internal_transfer_poll)

    @patch("core.rebalance_engine.REBALANCE_DEPOSIT_POLL_INTERVAL_SEC", 0)
    @patch("core.rebalance_engine.REBALANCE_INTERNAL_TRANSFER_POLL_INTERVAL_SEC", 0)
    def test_bybit_internal_transfer_polled_until_complete(self):
        eng = self._setup_deposit_poll(to_ex="bybit")
        self.mock_engine.bybit.find_deposit_by_amount_and_address.return_value = {"id": "d1"}
        self.mock_engine.bybit.transfer_funding_to_unified.return_value = {"transfer_id": "t1"}
        
        # Tick 1: detects deposit, calls transfer_funding_to_unified
        eng.tick(time.time() + 1)
        self.assertIsNotNone(eng._live_internal_transfer_poll)
        self.assertFalse(eng._live_internal_transfer_poll["assume_complete"])
        
        # Tick 2: polls status -> waiting
        self.mock_engine.bybit.get_internal_transfer_status.return_value = {"status": "processing"}
        res = eng.tick(time.time() + 2)
        self.assertEqual(res, "waiting")
        self.assertTrue(eng._is_rebalancing)
        
        # Tick 3: polls status -> complete
        self.mock_engine.bybit.get_internal_transfer_status.return_value = {"status": "complete"}
        res = eng.tick(time.time() + 3)
        self.assertEqual(res, "done")
        self.assertFalse(eng._is_rebalancing)
        self.assertIsNone(eng._live_internal_transfer_poll)


if __name__ == "__main__":
    unittest.main()