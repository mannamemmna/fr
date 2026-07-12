import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.rebalance_engine import RebalanceEngine


def _write_log(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class ResumeFromLogTests(unittest.TestCase):
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

    def test_paper_mode_never_resumes(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_submitted", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "x", "withdraw_id": "w1"},
        ])
        eng = RebalanceEngine(self.mock_engine, paper_mode=True)
        eng.resume_from_log()
        self.assertFalse(eng._is_rebalancing)

    def test_no_log_file_is_noop(self):
        eng = self._make()
        eng.resume_from_log()
        self.assertFalse(eng._is_rebalancing)

    def test_dry_run_chain_never_resumed(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "dry_run": True,
             "from": "bybit", "to": "kucoin", "token": "USDT", "network": "TRON",
             "amount": 50, "address": "x"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertFalse(eng._is_rebalancing)

    def test_terminal_chain_never_resumed(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "x"},
            {"client_id": "a", "ts": 2, "type": "internal_transfer_complete", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "x"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertFalse(eng._is_rebalancing)

    def test_resumes_withdraw_poll_with_same_withdraw_id(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "x"},
            {"client_id": "a", "ts": 2, "type": "withdraw_submitted", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "x", "withdraw_id": "w-123"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertTrue(eng._is_rebalancing)
        self.assertIsNotNone(eng._live_withdraw_poll)
        self.assertEqual(eng._live_withdraw_poll["withdraw_id"], "w-123")
        self.assertIs(eng._live_withdraw_poll["client"], self.mock_engine.bybit)

    def test_resumes_deposit_poll_phase(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "kc-addr"},
            {"client_id": "a", "ts": 2, "type": "withdraw_complete_awaiting_deposit", "from": "bybit",
             "to": "kucoin", "token": "USDT", "network": "TRON", "amount": 50, "address": "kc-addr"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertTrue(eng._is_rebalancing)
        self.assertIsNotNone(eng._live_deposit_poll)
        self.assertEqual(eng._live_deposit_poll["address"], "kc-addr")
        self.assertIs(eng._live_deposit_poll["client"], self.mock_engine.kucoin)

    def test_resumes_bybit_internal_transfer_poll(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "from": "kucoin", "to": "bybit",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "bb-addr"},
            {"client_id": "a", "ts": 2, "type": "internal_transfer_submitted", "from": "kucoin",
             "to": "bybit", "token": "USDT", "network": "TRON", "amount": 50, "address": "bb-addr",
             "transfer_id": "tid-1"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertTrue(eng._is_rebalancing)
        self.assertIsNotNone(eng._live_internal_transfer_poll)
        self.assertEqual(eng._live_internal_transfer_poll["transfer_id"], "tid-1")
        self.assertFalse(eng._live_internal_transfer_poll["assume_complete"])

    def test_kucoin_internal_transfer_submitted_assumed_complete_not_resubmitted(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "kc-addr"},
            {"client_id": "a", "ts": 2, "type": "internal_transfer_submitted", "from": "bybit",
             "to": "kucoin", "token": "USDT", "network": "TRON", "amount": 50, "address": "kc-addr",
             "transfer_id": "tid-2"},
        ])
        eng = self._make()
        eng.resume_from_log()
        # Must NOT call transfer_main_to_futures again
        self.mock_engine.kucoin.transfer_main_to_futures.assert_not_called()
        self.assertFalse(eng._is_rebalancing)  # treated as already complete

    def test_ambiguous_deposit_confirmed_phase_not_auto_resumed(self):
        _write_log(self._log_path, [
            {"client_id": "a", "ts": 1, "type": "withdraw_initiated", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "kc-addr"},
            {"client_id": "a", "ts": 2, "type": "deposit_confirmed", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "kc-addr"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertFalse(eng._is_rebalancing)  # NOT auto-resumed — must not guess
        self.mock_engine.kucoin.transfer_main_to_futures.assert_not_called()
        self.mock_engine.bybit.transfer_funding_to_unified.assert_not_called()

    def test_only_most_recent_chain_considered(self):
        _write_log(self._log_path, [
            {"client_id": "old", "ts": 1, "type": "withdraw_submitted", "from": "bybit", "to": "kucoin",
             "token": "USDT", "network": "TRON", "amount": 50, "address": "x", "withdraw_id": "w-old"},
            {"client_id": "old", "ts": 2, "type": "internal_transfer_complete", "from": "bybit",
             "to": "kucoin", "token": "USDT", "network": "TRON", "amount": 50, "address": "x"},
            {"client_id": "new", "ts": 3, "type": "withdraw_submitted", "from": "kucoin", "to": "bybit",
             "token": "USDT", "network": "TRON", "amount": 30, "address": "y", "withdraw_id": "w-new"},
        ])
        eng = self._make()
        eng.resume_from_log()
        self.assertTrue(eng._is_rebalancing)
        self.assertEqual(eng._live_withdraw_poll["withdraw_id"], "w-new")

    def test_corrupted_line_skipped_not_fatal(self):
        with open(self._log_path, "w") as f:
            f.write("{not valid json\n")
            f.write(json.dumps({"client_id": "a", "ts": 1, "type": "withdraw_submitted",
                                  "from": "bybit", "to": "kucoin", "token": "USDT", "network": "TRON",
                                  "amount": 50, "address": "x", "withdraw_id": "w-ok"}) + "\n")
        eng = self._make()
        eng.resume_from_log()  # must not raise
        self.assertTrue(eng._is_rebalancing)
        self.assertEqual(eng._live_withdraw_poll["withdraw_id"], "w-ok")


if __name__ == "__main__":
    unittest.main()