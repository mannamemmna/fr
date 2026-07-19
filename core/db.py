"""SQLite local database — event log, delisting blacklist, delisting checkpoints.

Thread-safe with WAL mode. Single file at DB_PATH.

Previously also defined trade_log and funding_snapshot tables plus
log_trade()/save_funding_snapshot()/recent_events() methods -- confirmed
unused by every caller in the app (trade history comes from
execution_log.jsonl / live_execution_log.jsonl instead) and removed.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR, DB_PATH

log = logging.getLogger("fr-bot.db")

# ─── Path ───────────────────────────────────────────────────────────────────
FULL_DB_PATH = DATA_DIR / Path(DB_PATH)
DATA_DIR.mkdir(exist_ok=True)
TRANSFER_LOG_FILE = os.path.join(DATA_DIR, "rebalance_transfers.jsonl")


class LocalDB:
    """Thread-safe SQLite store. One connection per thread via local."""

    _local = threading.local()
    _lock = threading.Lock()

    def __init__(self):
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(FULL_DB_PATH), timeout=10)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init(self):
        """Create tables if not exist. Called once at startup."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS delisting_blacklist (
                symbol TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                confidence TEXT NOT NULL,
                reason TEXT,
                delist_ts INTEGER,
                announcement_url TEXT,
                source_title TEXT,
                detected_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS delisting_checkpoint (
                exchange TEXT PRIMARY KEY,
                last_seen_ts INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_event_ts ON event_log(ts DESC);
        """)
        conn.commit()

    # ─── Event log ────────────────────────────────────────────────────

    def log_event(self, level: str, source: str, message: str, details: Any = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO event_log (ts, level, source, message, details) VALUES (?,?,?,?,?)",
            (time.time(), level, source, message, json.dumps(details) if details else None),
        )
        conn.commit()

    # ─── Delisting blacklist ──────────────────────────────────────────

    def add_to_blacklist(self, symbol: str, exchange: str, confidence: str,
                          reason: str, delist_ts: Optional[int],
                          announcement_url: str, source_title: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute(
            """INSERT OR IGNORE INTO delisting_blacklist
               (symbol, exchange, confidence, reason, delist_ts, announcement_url, source_title, detected_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (symbol.upper(), exchange, confidence, reason, delist_ts,
             announcement_url, source_title, time.time()),
        )
        conn.commit()
        return cur.rowcount > 0

    def remove_from_blacklist(self, symbol: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM delisting_blacklist WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
        return cur.rowcount > 0

    def is_blacklisted(self, symbol: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM delisting_blacklist WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
        return row is not None

    def get_blacklist(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM delisting_blacklist ORDER BY detected_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_blacklisted_symbols(self) -> set[str]:
        conn = self._get_conn()
        rows = conn.execute("SELECT symbol FROM delisting_blacklist").fetchall()
        return {r["symbol"] for r in rows}

    def get_delisting_checkpoint(self, exchange: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT last_seen_ts FROM delisting_checkpoint WHERE exchange = ?", (exchange,)
        ).fetchone()
        return row["last_seen_ts"] if row else 0

    def set_delisting_checkpoint(self, exchange: str, ts: int):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO delisting_checkpoint (exchange, last_seen_ts) VALUES (?,?) "
            "ON CONFLICT(exchange) DO UPDATE SET last_seen_ts = excluded.last_seen_ts",
            (exchange, ts),
        )
        conn.commit()

    # ─── Rebalance Transfers ──────────────────────────────────────────────

    def get_recent_transfers(self, limit: int = 5) -> list[dict]:
        if not os.path.exists(TRANSFER_LOG_FILE):
            return []
        results = []
        with open(TRANSFER_LOG_FILE, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        results.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        pass
        results.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return results[:limit]


_db_instance: Optional[LocalDB] = None


def get_db() -> LocalDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = LocalDB()
        _db_instance._init()
    return _db_instance
