import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TransferLogRegressionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._log_path = os.path.join(self._tmpdir.name, "rebalance_transfers.jsonl")

    def test_get_recent_transfers_no_file_returns_empty(self):
        from core.db import LocalDB
        with patch("core.db.TRANSFER_LOG_FILE", self._log_path):
            self.assertEqual(LocalDB().get_recent_transfers(), [])

    def test_get_recent_transfers_reads_and_sorts_by_ts(self):
        from core.db import LocalDB
        rows = [
            {"client_id": "a", "ts": 100, "type": "withdraw_initiated"},
            {"client_id": "a", "ts": 200, "type": "withdraw_submitted"},
        ]
        with open(self._log_path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        with patch("core.db.TRANSFER_LOG_FILE", self._log_path):
            result = LocalDB().get_recent_transfers(5)
        self.assertEqual(result[0]["ts"], 200)  # most recent first
        self.assertEqual(len(result), 2)

    def test_get_recent_transfers_respects_limit(self):
        from core.db import LocalDB
        with open(self._log_path, "w") as f:
            for i in range(10):
                f.write(json.dumps({"client_id": "a", "ts": i, "type": "x"}) + "\n")
        with patch("core.db.TRANSFER_LOG_FILE", self._log_path):
            result = LocalDB().get_recent_transfers(3)
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()