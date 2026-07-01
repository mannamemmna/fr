"""Spread & Funding Difference Engine — event-driven.

Listens to market_cache updates and computes:
  - Price Spread: ((P_Long - P_Short) / P_Short) * 100%
  - Funding Diff: normalized difference between Bybit & KuCoin funding rates
  - Signal generation for automation engine

Pure computation — no I/O. Runs in caller's thread.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from config import (
    AUTO_DELTA_THRESHOLD,
    AUTO_ENTRY_WINDOW_MIN,
)
from core.market_cache import PriceCache, FundingCache
from core.db import get_db

log = logging.getLogger("fr-bot.spread")

# ─── Signal types ───────────────────────────────────────────────────────────

SpreadSignal = dict  # type alias — see get_signals() below

# ─── Callback ───────────────────────────────────────────────────────────────

SignalCallback = Callable[[str, SpreadSignal], None]  # (symbol, signal)


# ─── Spread Engine ──────────────────────────────────────────────────────────

class SpreadEngine:
    """Listens to market data and computes opportunities in real-time.

    This replaces the old scanner-based polling approach.
    Data flows: WS → caches → spread_engine → automation_engine.
    """

    def __init__(self, price_cache: PriceCache, funding_cache: FundingCache):
        self._price = price_cache
        self._funding = funding_cache
        self._signal_callback: Optional[SignalCallback] = None
        self._last_signals: dict[str, SpreadSignal] = {}  # symbol -> latest signal

    def set_signal_callback(self, cb: SignalCallback):
        """Callback invoked when a pair crosses thresholds."""
        self._signal_callback = cb

    # ─── Public API ───────────────────────────────────────────────────

    def on_funding_update(self, exchange: str, data: dict):
        """Called by ws_pool or external whenever funding data arrives.

        data: {symbol, funding_rate, next_funding_ts, interval_h}
        """
        symbol = data.get("symbol", "")
        if not symbol:
            return
        signal = self._compute(symbol)
        if signal:
            self._last_signals[symbol] = signal
            self._emit_signal(symbol, signal)

    def compute_signal(self, symbol: str) -> Optional[SpreadSignal]:
        """Compute current signal for one symbol."""
        return self._compute(symbol)

    def get_all_signals(self) -> dict[str, SpreadSignal]:
        """Return latest computed signals for all pairs."""
        return dict(self._last_signals)

    def get_signal(self, symbol: str) -> Optional[SpreadSignal]:
        return self._last_signals.get(symbol)

    def clear_signals(self, symbol: Optional[str] = None):
        if symbol:
            self._last_signals.pop(symbol, None)
        else:
            self._last_signals.clear()

    # ─── Core computation ─────────────────────────────────────────────

    def _compute(self, symbol: str) -> Optional[SpreadSignal]:
        """Compute opportunity signal for one pair. Returns None if data incomplete."""

        # Prices
        bybit_price = self._price.get_price("bybit", symbol)
        kucoin_price = self._price.get_price("kucoin", symbol)
        if bybit_price <= 0 or kucoin_price <= 0:
            return None

        # Funding
        bb_funding = self._funding.get(symbol, "bybit")
        kc_funding = self._funding.get(symbol, "kucoin")
        if not bb_funding or not kc_funding:
            return None

        bb_rate = bb_funding.get("funding_rate", 0.0)
        kc_rate = kc_funding.get("funding_rate", 0.0)
        bb_iv = int(bb_funding.get("interval_h", 8) or 8)
        kc_iv = int(kc_funding.get("interval_h", 8) or 8)
        bb_next_ts = bb_funding.get("next_funding_ts", 0) or 0
        kc_next_ts = kc_funding.get("next_funding_ts", 0) or 0
        bb_next_pay = bb_funding.get("next_payment_rate", bb_rate)
        kc_next_pay = kc_funding.get("next_payment_rate", kc_rate)

        raw_fr_diff = bb_rate - kc_rate

        # Direction & actions
        if raw_fr_diff > 0:
            direction = "SHORT Bybit / LONG KuCoin"
            bybit_action, kucoin_action = "SHORT", "LONG"
            p_short = bybit_price
            p_long = kucoin_price
        elif raw_fr_diff < 0:
            direction = "SHORT KuCoin / LONG Bybit"
            bybit_action, kucoin_action = "LONG", "SHORT"
            p_short = kucoin_price
            p_long = bybit_price
        else:
            direction = "FLAT"
            bybit_action, kucoin_action = "—", "—"
            p_short = p_long = bybit_price

        # Price Spread
        price_spread = ((p_long - p_short) / p_short) * 100.0 if p_short > 0 else 0.0

        # Normalized Funding Diff (per-jam)
        bb_norm = bb_rate / max(bb_iv, 1)   # rate per jam
        kc_norm = kc_rate / max(kc_iv, 1)   # rate per jam
        raw_diff = bb_norm - kc_norm
        funding_diff_pct = abs(raw_diff) * 100.0

        # Annualized
        per_day = 24.0
        net_daily = funding_diff_pct / 100.0 * per_day
        annual_pct = net_daily * 365.0 * 100.0

        # Funding timing
        now = time.time()
        bb_next_sec = bb_next_ts // 1000 if bb_next_ts > 1e10 else bb_next_ts
        kc_next_sec = kc_next_ts // 1000 if kc_next_ts > 1e10 else kc_next_ts
        win = AUTO_ENTRY_WINDOW_MIN * 60
        in_window = (0 < bb_next_sec - now <= win) or (0 < kc_next_sec - now <= win)

        signal = {
            "symbol": symbol,
            "bybit_price": round(bybit_price, 8),
            "kucoin_price": round(kucoin_price, 8),
            "price_spread_pct": round(price_spread, 6),
            "bybit_rate_pct": round(bb_rate * 100, 6),
            "kucoin_rate_pct": round(kc_rate * 100, 6),
            "bybit_next_pay_pct": round(bb_next_pay * 100, 6),
            "kucoin_next_pay_pct": round(kc_next_pay * 100, 6),
            "raw_fr_diff": round(raw_fr_diff * 100, 6),
            "funding_diff_pct": round(funding_diff_pct, 6),
            "delta_pct": round(funding_diff_pct, 6),  # alias for backwards compat
            "net_daily_pct": round(net_daily * 100, 4),
            "annual_pct": round(annual_pct, 2),
            "direction": direction,
            "bybit_action": bybit_action,
            "kucoin_action": kucoin_action,
            "bybit_interval_h": bb_iv,
            "kucoin_interval_h": kc_iv,
            "bybit_next_ts": bb_next_sec,
            "kucoin_next_ts": kc_next_sec,
            "in_funding_window": in_window,
        }

        return signal

    def _emit_signal(self, symbol: str, signal: SpreadSignal):
        """Invoke callback if registered."""
        if self._signal_callback:
            try:
                self._signal_callback(symbol, signal)
            except Exception:
                log.exception("Signal callback failed for %s", symbol)


# ─── Singleton ──────────────────────────────────────────────────────────────

_engine: Optional[SpreadEngine] = None


def get_spread_engine() -> SpreadEngine:
    global _engine
    if _engine is None:
        from core.market_cache import get_price_cache, get_funding_cache
        _engine = SpreadEngine(get_price_cache(), get_funding_cache())
    return _engine