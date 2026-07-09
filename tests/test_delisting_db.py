import os
import tempfile
import unittest
from unittest.mock import patch


class DelistingBlacklistDbTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._tmpdir.name, "test.db")
        patcher = patch("core.db.FULL_DB_PATH", self._db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)

        from core.db import LocalDB
        self.db = LocalDB()
        self.db._init()

    def test_add_and_check_blacklist(self):
        is_new = self.db.add_to_blacklist(
            symbol="AKRO", exchange="bybit", confidence="high",
            reason="test delisting", delist_ts=None,
            announcement_url="https://example.com", source_title="test title",
        )
        self.assertTrue(is_new)
        self.assertTrue(self.db.is_blacklisted("akro"))  # case-insensitive
        self.assertIn("AKRO", self.db.get_blacklisted_symbols())

    def test_duplicate_insert_returns_false(self):
        self.db.add_to_blacklist("AKRO", "bybit", "high", "r1", None, "url1", "t1")
        is_new_again = self.db.add_to_blacklist("AKRO", "bybit", "high", "r2", None, "url2", "t2")
        self.assertFalse(is_new_again)

    def test_remove_from_blacklist(self):
        self.db.add_to_blacklist("AKRO", "bybit", "high", "r", None, "url", "t")
        removed = self.db.remove_from_blacklist("AKRO")
        self.assertTrue(removed)
        self.assertFalse(self.db.is_blacklisted("AKRO"))

    def test_remove_nonexistent_returns_false(self):
        self.assertFalse(self.db.remove_from_blacklist("NOTEXIST"))

    def test_checkpoint_persists(self):
        self.db.set_delisting_checkpoint("bybit", 12345)
        self.assertEqual(self.db.get_delisting_checkpoint("bybit"), 12345)
        self.db.set_delisting_checkpoint("bybit", 99999)  # update, bukan insert baru
        self.assertEqual(self.db.get_delisting_checkpoint("bybit"), 99999)

    def test_unknown_exchange_checkpoint_defaults_zero(self):
        self.assertEqual(self.db.get_delisting_checkpoint("unknown"), 0)


if __name__ == "__main__":
    unittest.main()