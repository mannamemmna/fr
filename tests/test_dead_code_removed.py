"""Dead code removal — regression guards.

Proves the removed code is actually gone and the surviving surface still works.
Uses AST parsing (not string grep) so docstrings mentioning "ccxt" can't cause
false failures.
"""

import ast
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


class DeadCodeRemovedTests(unittest.TestCase):
    def _py_files(self, exclude_tests=True):
        """All .py files under REPO_ROOT, optionally excluding tests/."""
        for root, dirs, files in os.walk(REPO_ROOT):
            if exclude_tests and "tests" in root.split(os.sep):
                continue
            if ".venv" in root.split(os.sep) or "__pycache__" in root.split(os.sep):
                continue
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)

    def test_no_file_imports_ccxt(self):
        """No .py file outside tests/ imports ccxt (AST-verified)."""
        violators = []
        for path in self._py_files():
            try:
                with open(path) as fh:
                    tree = ast.parse(fh.read(), path)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "ccxt":
                            violators.append(f"{path}: import ccxt")
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "ccxt" or (node.module and node.module.startswith("ccxt.")):
                        violators.append(f"{path}: from {node.module} import ...")
        self.assertEqual(violators, [], f"ccxt still imported in: {violators}")

    def test_keys_helper_file_gone(self):
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "exchanges", "keys_helper.py")))

    def test_keys_file_gone(self):
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "core", "keys.py")))

    def test_ws_connection_has_no_send(self):
        from core.ws_pool import WSConnection
        self.assertFalse(hasattr(WSConnection, "send"))

    def test_kucoin_live_has_no_internal_transfer_status(self):
        from exchanges.kucoin_live import KuCoinLiveClient
        self.assertFalse(hasattr(KuCoinLiveClient, "get_internal_transfer_status"))

    def test_local_db_has_no_removed_methods(self):
        from core.db import LocalDB
        self.assertFalse(hasattr(LocalDB, "log_trade"))
        self.assertFalse(hasattr(LocalDB, "save_funding_snapshot"))
        self.assertFalse(hasattr(LocalDB, "recent_events"))

    def test_local_db_init_creates_only_active_tables(self):
        import sqlite3
        from core.db import LocalDB
        # Use in-memory DB to avoid stale tables from disk
        db = LocalDB()
        db._local = type(db._local)()  # fresh local for in-memory
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db._get_conn = lambda: conn
        db._init()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        self.assertIn("event_log", tables)
        self.assertIn("delisting_blacklist", tables)
        self.assertIn("delisting_checkpoint", tables)
        self.assertNotIn("trade_log", tables)
        self.assertNotIn("funding_snapshot", tables)

    def test_bybit_client_instantiable_and_has_readonly_methods(self):
        from exchanges.bybit import BybitClient
        c = BybitClient()
        self.assertEqual(c.name, "bybit")
        self.assertFalse(hasattr(c, "ccxt_id"))
        self.assertTrue(hasattr(c, "fetch_all_funding_rates"))
        self.assertTrue(hasattr(c, "fetch_ticker"))
        self.assertFalse(hasattr(c, "place_market_order"))
        self.assertFalse(hasattr(c, "close_position"))
        self.assertFalse(hasattr(c, "fetch_positions"))
        self.assertFalse(hasattr(c, "test_credentials"))

    def test_kucoin_client_instantiable_and_has_readonly_methods(self):
        from exchanges.kucoin import KuCoinClient
        c = KuCoinClient()
        self.assertEqual(c.name, "kucoin")
        self.assertFalse(hasattr(c, "ccxt_id"))
        self.assertTrue(hasattr(c, "fetch_all_funding_rates"))
        self.assertTrue(hasattr(c, "fetch_ticker"))
        self.assertFalse(hasattr(c, "place_market_order"))
        self.assertFalse(hasattr(c, "close_position"))
        self.assertFalse(hasattr(c, "fetch_positions"))
        self.assertFalse(hasattr(c, "test_credentials"))

    def test_get_client_factory_works(self):
        from exchanges.bybit import BybitClient
        from exchanges.kucoin import KuCoinClient
        from exchanges.base import BaseExchangeClient
        clients = {"bybit": BybitClient(), "kucoin": KuCoinClient()}
        for name, c in clients.items():
            self.assertIsInstance(c, BaseExchangeClient)
            self.assertEqual(c.name, name)

    def test_event_log_still_works(self):
        from core.db import LocalDB
        db = LocalDB()
        db._init()
        db.log_event("INFO", "test", "test message")
        # Should not raise

    def test_delisting_blacklist_still_works(self):
        from core.db import LocalDB
        db = LocalDB()
        db._init()
        db.add_to_blacklist("TESTDELIST", "bybit", "high", "test", None, "", "test title")
        self.assertTrue(db.is_blacklisted("TESTDELIST"))
        db.remove_from_blacklist("TESTDELIST")
        self.assertFalse(db.is_blacklisted("TESTDELIST"))

    def test_requirements_no_ccxt_no_dupes(self):
        req_path = os.path.join(REPO_ROOT, "requirements.txt")
        with open(req_path) as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        # No ccxt
        self.assertFalse(any("ccxt" in l for l in lines), "ccxt found in requirements.txt")
        # No duplicates
        seen = {}
        for l in lines:
            pkg = l.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
            self.assertNotIn(pkg, seen, f"Duplicate package in requirements.txt: {pkg}")
            seen[pkg] = True


if __name__ == "__main__":
    unittest.main()