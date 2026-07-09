"""SQLite local database — event log, trade log, error log, funding snapshots.

Thread-safe with WAL mode. Single file at DB_PATH.
"""

from __future__ import annotations

import json
import logging
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
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                type TEXT NOT NULL,           -- 'entry', 'close', 'cancel'
                symbol TEXT NOT NULL,
                side_bybit TEXT,
                side_kucoin TEXT,
                amount_usd REAL,
                leverage INTEGER,
                entry_spread REAL,
                entry_diff_fr REAL,
                exit_spread REAL,
                exit_diff_fr REAL,
                price_pnl REAL,
                funding_pnl REAL,
                fees REAL,
                realized_pnl REAL,
                balance_after REAL,
                paper INTEGER DEFAULT 1,
                details TEXT                   -- JSON extra
            );

            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                level TEXT NOT NULL,           -- 'INFO', 'WARN', 'ERROR'
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS funding_snapshot (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        funding_rate REAL,
                        next_payment_rate REAL,
                        mark_price REAL,
                        next_funding_ts INTEGER
                    );

                    CREATE TABLE IF NOT EXISTS delisting_blacklist (
                        symbol TEXT PRIMARY KEY,
                        exchange TEXT NOT NULL,
                        confidence TEXT NOT NULL,      -- 'high' | 'manual'
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
                    CREATE INDEX IF NOT EXISTS idx_trade_ts ON trade_log(ts DESC);
                    CREATE INDEX IF NOT EXISTS idx_funding_sym ON funding_snapshot(symbol, ts DESC);
        """)
        conn.commit()

    # ─── Trade log ────────────────────────────────────────────────────

    def log_trade(self, trade_type: str, symbol: str, **kwargs):
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO trade_log (ts, type, symbol, side_bybit, side_kucoin,
               amount_usd, leverage, entry_spread, entry_diff_fr,
               exit_spread, exit_diff_fr, price_pnl, funding_pnl,
               fees, realized_pnl, balance_after, paper, details)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                trade_type,
                symbol,
                kwargs.get("side_bybit"),
                kwargs.get("side_kucoin"),
                kwargs.get("amount_usd"),
                kwargs.get("leverage"),
                kwargs.get("entry_spread"),
                kwargs.get("entry_diff_fr"),
                kwargs.get("exit_spread"),
                kwargs.get("exit_diff_fr"),
                kwargs.get("price_pnl"),
                kwargs.get("funding_pnl"),
                kwargs.get("fees"),
                kwargs.get("realized_pnl"),
                kwargs.get("balance_after"),
                1 if kwargs.get("paper", True) else 0,
                json.dumps({k: v for k, v in kwargs.items() if v is not None}) if kwargs else None,
            ),
        )
        conn.commit()

    # ─── Event log ────────────────────────────────────────────────────

    def log_event(self, level: str, source: str, message: str, details: Any = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO event_log (ts, level, source, message, details) VALUES (?,?,?,?,?)",
            (time.time(), level, source, message, json.dumps(details) if details else None),
        )
        conn.commit()

    def recent_events(self, limit: int = 50, level: Optional[str] = None) -> list[dict]:
        conn = self._get_conn()
        if level:
            rows = conn.execute(
                "SELECT * FROM event_log WHERE level=? ORDER BY ts DESC LIMIT ?",
                (level, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM event_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_funding_snapshot(self, exchange: str, symbol: str,
                              funding_rate: float, next_payment_rate: float,
                              mark_price: float, next_funding_ts: int):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO funding_snapshot (ts, exchange, symbol, funding_rate, next_payment_rate, mark_price, next_funding_ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (time.time(), exchange, symbol, funding_rate, next_payment_rate, mark_price, next_funding_ts),
        )
        conn.commit()

    # ─── Delisting blacklist ──────────────────────────────────────────

    def add_to_blacklist(self, symbol: str, exchange: str, confidence: str,
                          reason: str, delist_ts: Optional[int],
                          announcement_url: str, source_title: str) -> bool:
        """Insert kalau simbol belum ada di blacklist. Returns True kalau ini
        entry BARU (perlu alert), False kalau sudah ada (skip re-alert)."""
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

_db_instance: Optional[LocalDB] = None


def get_db() -> LocalDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = LocalDB()
        _db_instance._init()
    return _db_instance