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

            CREATE INDEX IF NOT EXISTS idx_event_ts ON event_log(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_trade_ts ON trade_log(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_funding_sym ON funding_snapshot(symbol, ts DESC);

            CREATE TABLE IF NOT EXISTS rebalance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                trigger TEXT NOT NULL,
                drift_before REAL,
                drift_after REAL,
                margin_ratio_before REAL,
                margin_ratio_after REAL,
                qty_before_bb REAL,
                qty_after_bb REAL,
                qty_before_kc REAL,
                qty_after_kc REAL,
                fee_paid REAL,
                paper INTEGER DEFAULT 1,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_rebalance_ts ON rebalance_log(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_rebalance_pos ON rebalance_log(position_id);
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


    # ─── Rebalance log ──────────────────────────────────────────────────

    def log_rebalance(self, position_id: str, symbol: str, action: str,
                      trigger: str, **kwargs):
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO rebalance_log (ts, position_id, symbol, action, trigger,
               drift_before, drift_after, margin_ratio_before, margin_ratio_after,
               qty_before_bb, qty_after_bb, qty_before_kc, qty_after_kc,
               fee_paid, paper, details)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                position_id,
                symbol,
                action,
                trigger,
                kwargs.get("drift_before"),
                kwargs.get("drift_after"),
                kwargs.get("margin_ratio_before"),
                kwargs.get("margin_ratio_after"),
                kwargs.get("qty_before_bb"),
                kwargs.get("qty_after_bb"),
                kwargs.get("qty_before_kc"),
                kwargs.get("qty_after_kc"),
                kwargs.get("fee_paid", 0),
                1 if kwargs.get("paper", True) else 0,
                json.dumps({k: v for k, v in kwargs.items() if v is not None}) if kwargs else None,
            ),
        )
        conn.commit()

    def recent_rebalances(self, limit: int = 10) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM rebalance_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Singleton ──────────────────────────────────────────────────────────────

_db_instance: Optional[LocalDB] = None


def get_db() -> LocalDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = LocalDB()
        _db_instance._init()
    return _db_instance