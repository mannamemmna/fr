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
    """Current mark prices keyed by unified symbol."""

    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict[str, dict] = {}  # symbol -> {bybit: float, kucoin: float, ts: float}

    def update(self, exchange: str, symbol: str, mark_price: float):
        with self._lock:
            if symbol not in self._store:
                self._store[symbol] = {"bybit": 0.0, "kucoin": 0.0, "ts": 0}
            self._store[symbol][exchange] = mark_price
            self._store[symbol]["ts"] = time.time()

    def get(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._store.get(symbol)

    def get_price(self, exchange: str, symbol: str) -> float:
        entry = self.get(symbol)
        if entry:
            return entry.get(exchange, 0.0)
        return 0.0

    def all_symbols(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def age(self, symbol: str) -> Optional[float]:
        """Seconds since last update, or None if never seen."""
        entry = self.get(symbol)
        if entry and entry["ts"]:
            return time.time() - entry["ts"]
        return None


class FundingCache:
    """Current funding rate info keyed by unified symbol."""

    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict[str, dict] = {}  # symbol -> {bybit: FundingInfo, kucoin: FundingInfo}

    def update(self, exchange: str, symbol: str,
               funding_rate: float, next_payment_rate: float,
               next_funding_ts: int, interval_h: int):
        from exchanges.base import FundingRate  # noqa: keep type clear
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

    def get_both(self, symbol: str) -> tuple[Optional[dict], Optional[dict]]:
        """Returns (bybit_info, kucoin_info) or (None, None)."""
        with self._lock:
            entry = self._store.get(symbol)
            if not entry:
                return None, None
            return entry.get("bybit"), entry.get("kucoin")


# ─── Singleton ──────────────────────────────────────────────────────────────

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