"""Real-time market data cache — price + funding from WebSocket.

Thread-safe in-memory store. Updated by ws_pool callbacks.
Read by spread_engine and automation_engine.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger("fr-bot.market_cache")


class PriceCache:
    """Current bid/ask/mark prices keyed by unified symbol.

    bid/ask are what actually gets used for price-spread math (see
    core/spread_engine.py and core/automation_engine.py) — a market SELL
    fills at the bid, a market BUY fills at the ask, so using those
    instead of mark price makes the spread reflect real execution cost.
    mark is kept for display purposes (e.g. /pair, /portfolio) where
    showing the reference/index price is still useful and unambiguous.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict[str, dict] = {}

    def _entry(self, symbol: str) -> dict:
        if symbol not in self._store:
            self._store[symbol] = {
                "bybit": {"bid": 0.0, "ask": 0.0, "mark": 0.0},
                "kucoin": {"bid": 0.0, "ask": 0.0, "mark": 0.0},
                "ts": 0.0,
            }
        return self._store[symbol]

    def update(self, exchange: str, symbol: str, *,
               bid: Optional[float] = None, ask: Optional[float] = None,
               mark: Optional[float] = None):
        if bid is None and ask is None and mark is None:
            return
        with self._lock:
            side = self._entry(symbol)[exchange]
            if bid is not None:
                side["bid"] = bid
            if ask is not None:
                side["ask"] = ask
            if mark is not None:
                side["mark"] = mark
            self._store[symbol]["ts"] = time.time()

    def get(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._store.get(symbol)

    def get_price(self, exchange: str, symbol: str) -> float:
        """Mark price. Display/back-compat only — spread math uses
        get_bid_ask() instead."""
        entry = self.get(symbol)
        if entry:
            return entry.get(exchange, {}).get("mark", 0.0)
        return 0.0

    def get_bid_ask(self, exchange: str, symbol: str) -> tuple[float, float]:
        """Returns (bid, ask), each 0.0 if not yet known."""
        entry = self.get(symbol)
        if entry:
            side = entry.get(exchange, {})
            return side.get("bid", 0.0), side.get("ask", 0.0)
        return 0.0, 0.0

    def all_symbols(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def age(self, symbol: str) -> Optional[float]:
        entry = self.get(symbol)
        if entry and entry["ts"]:
            return time.time() - entry["ts"]
        return None


class FundingCache:
    """Current funding rate info keyed by unified symbol."""

    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict[str, dict] = {}

    def update(self, exchange: str, symbol: str,
               funding_rate: float, next_payment_rate: float,
               next_funding_ts: int, interval_h: int):
        with self._lock:
            if symbol not in self._store:
                self._store[symbol] = {}
            self._store[symbol][exchange] = {
                "funding_rate": funding_rate,
                "next_payment_rate": next_payment_rate,
                "next_funding_ts": next_funding_ts,
                "interval_h": interval_h,
                "ts": time.time(),
            }

    def get(self, symbol: str, exchange: str) -> Optional[dict]:
        with self._lock:
            entry = self._store.get(symbol)
            if entry:
                return entry.get(exchange)
            return None

    def all_keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())


# ─── Singletons ──────────────────────────────────────────────────────────────

_price_cache: Optional[PriceCache] = None
_funding_cache: Optional[FundingCache] = None


def get_price_cache() -> PriceCache:
    global _price_cache
    if _price_cache is None:
        _price_cache = PriceCache()
    return _price_cache


def get_funding_cache() -> FundingCache:
    global _funding_cache
    if _funding_cache is None:
        _funding_cache = FundingCache()
    return _funding_cache
