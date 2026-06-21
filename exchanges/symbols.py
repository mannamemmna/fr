"""Symbol normalisation across exchanges.

Unified format: BASE/USDT:USDT  (ccxt swap convention)

Examples:
    Bybit "BTCUSDT"        → "BTC/USDT:USDT"
    Bybit "1000PEPEUSDT"   → "PEPE/USDT:USDT"   (strip multiplier)
    Bybit "SHIB1000USDT"   → "SHIB/USDT:USDT"   (suffix multiplier)
    KuCoin "XBTUSDTM"      → "BTC/USDT:USDT"    (XBT → BTC)
    KuCoin "PEPEUSDTM"     → "PEPE/USDT:USDT"
"""

from __future__ import annotations

from typing import Tuple

# ─── Canonical exchange names ──────────────────────────────────────────────
SUPPORTED_EXCHANGES: Tuple[str, ...] = ("bybit", "kucoin")


# ─── Low-level helpers ────────────────────────────────────────────────────

def _strip_usdt_suffix(s: str) -> str:
    """Remove trailing USDTM/USDT/PERP markers."""
    u = s.upper()
    for suf in ("USDTM", "USDT", "PERP", "-PERP"):
        if u.endswith(suf):
            return u[: -len(suf)]
    return u


def _strip_multiplier(s: str) -> str:
    """Strip 1000/10000/1000000 multipliers either as prefix or before USDT.

    Bybit conventions:
        1000PEPEUSDT   — prefix multiplier
        SHIB1000USDT   — suffix multiplier
    """
    for mult in ("1000000", "100000", "10000", "1000"):
        if s.startswith(mult):
            return s[len(mult):]
        if mult in s:
            return s.replace(mult, "")
    return s


def _base_symbol(s: str) -> str:
    """Normalise base across exchanges: XBT→BTC, 1000PEPE→PEPE."""
    base = _strip_usdt_suffix(s)
    base = _strip_multiplier(base)
    if base in ("XBT", "XXBT"):
        return "BTC"
    return base


# ─── Per-exchange → unified ────────────────────────────────────────────────

def bybit_to_unified(symbol: str) -> str:
    """BTCUSDT → BTC/USDT:USDT  ;  1000PEPEUSDT → PEPE/USDT:USDT."""
    return f"{_base_symbol(symbol)}/USDT:USDT"


def kucoin_to_unified(symbol: str) -> str:
    """XBTUSDTM → BTC/USDT:USDT  ;  PEPEUSDTM → PEPE/USDT:USDT."""
    return f"{_base_symbol(symbol)}/USDT:USDT"


def normalize_symbol(exchange: str, raw_symbol: str) -> str:
    """Dispatch to the right converter."""
    e = exchange.strip().lower()
    if e == "bybit":
        return bybit_to_unified(raw_symbol)
    if e == "kucoin":
        return kucoin_to_unified(raw_symbol)
    raise ValueError(f"Unknown exchange: {exchange}")


# ─── Unified → exchange-native (for ccxt order placement) ────────────────

def unified_to_native(exchange: str, unified_symbol: str) -> str:
    """BTC/USDT:USDT → BTCUSDT (Bybit) or XBTUSDTM (KuCoin)."""
    base = parse_unified(unified_symbol)
    e = exchange.strip().lower()
    if e == "bybit":
        return f"{base}USDT"
    if e == "kucoin":
        return f"{base}USDTM" if base != "BTC" else "XBTUSDTM"
    raise ValueError(f"Unknown exchange: {exchange}")


def parse_unified(unified_symbol: str) -> str:
    """BTC/USDT:USDT → BTC"""
    if "/" not in unified_symbol:
        return unified_symbol
    return unified_symbol.split("/")[0]


# ─── Cross-exchange matching ──────────────────────────────────────────────

def common_symbols(bybit_syms: list[str], kucoin_syms: list[str]) -> list[str]:
    """Return sorted list of base symbols (BTC, ETH, ...) available on both."""
    bb = {_base_symbol(s) for s in bybit_syms}
    kc = {_base_symbol(s) for s in kucoin_syms}
    return sorted(bb & kc)
