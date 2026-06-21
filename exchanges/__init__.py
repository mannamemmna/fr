"""Exchange client factory.

Public API:
    get_client(name)              → BaseExchangeClient instance
    list_supported()              → tuple of canonical exchange names
    normalize_symbol(name, sym)   → "BTC/USDT:USDT"
    parse_unified(sym)            → "BTC"
"""

from .base import BaseExchangeClient, FundingRate
from .bybit import BybitClient
from .kucoin import KuCoinClient
from .symbols import (
    SUPPORTED_EXCHANGES,
    normalize_symbol,
    parse_unified,
    kucoin_to_unified,
    bybit_to_unified,
)

_CLIENTS: dict[str, BaseExchangeClient] = {}


def get_client(name: str) -> BaseExchangeClient:
    """Return a cached client instance for *name* (case-insensitive)."""
    key = name.strip().lower()
    if key not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"Unsupported exchange '{name}'. Supported: {SUPPORTED_EXCHANGES}"
        )
    if key not in _CLIENTS:
        _CLIENTS[key] = {
            "bybit": BybitClient,
            "kucoin": KuCoinClient,
        }[key]()
    return _CLIENTS[key]


def list_supported() -> tuple[str, ...]:
    return SUPPORTED_EXCHANGES


__all__ = [
    "BaseExchangeClient",
    "FundingRate",
    "BybitClient",
    "KuCoinClient",
    "SUPPORTED_EXCHANGES",
    "normalize_symbol",
    "parse_unified",
    "get_client",
    "list_supported",
]
