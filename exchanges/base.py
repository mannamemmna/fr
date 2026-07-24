"""Abstract base for exchange clients.

Only covers the READ-ONLY surface (bulk funding rates + single ticker)
used by the scanner and /health. Authenticated trading goes through the
dedicated live clients (exchanges/bybit_live.py, exchanges/kucoin_live.py)
exclusively -- see README "Dead code is dangerous here" note. This class
previously also carried a ccxt-based authenticated trading path
(place_market_order/close_position/fetch_positions/test_credentials via
_ccxt()/_ccxt_with_keys()), confirmed unused by every caller in the app
and removed: it bypassed all of the live clients' safety machinery
(retry/idempotency, fill verification, partial-fill reconciliation,
instrument step-size lookup), so its mere presence was a risk that some
future change could get accidentally wired up to it instead of the real
live clients.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class FundingRate:
    """Normalized funding rate record. Same shape from every exchange."""

    symbol: str                # unified: "BTC/USDT:USDT"
    raw_symbol: str            # exchange-native: "BTCUSDT" / "XBTUSDTM"
    funding_rate: float        # current rate (paid at next funding)
    next_payment_rate: float   # rate that will be paid at next funding
    mark_price: Optional[float]
    index_price: Optional[float]
    funding_next_time: Optional[int]   # ms epoch (next funding timestamp)
    interval_hours: int        # 1, 4, or 8
    bid_price: Optional[float] = None  # best bid, if the bulk REST endpoint carries it (Bybit: yes; KuCoin: no)
    ask_price: Optional[float] = None  # best ask, same caveat

    @property
    def is_valid(self) -> bool:
        return self.funding_rate is not None


@dataclass
class Ticker:
    symbol: str
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    mark: Optional[float] = None
    index: Optional[float] = None
    ts: int = 0


class BaseExchangeClient(ABC):
    """Subclasses implement the two read-only methods below. That's the
    entire interface the rest of the app depends on for this class."""

    name: str = "base"

    @abstractmethod
    def fetch_all_funding_rates(self) -> dict[str, FundingRate]:
        """Bulk fetch all USDT-margined perp funding rates on this venue.
        Returns dict keyed by UNIFIED symbol "BASE/USDT:USDT"."""

    @abstractmethod
    def fetch_ticker(self, unified_symbol: str) -> Optional[Ticker]:
        """Live ticker for one symbol (unified)."""
