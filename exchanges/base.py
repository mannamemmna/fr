"""Abstract base for exchange clients.

All exchange clients (Bybit, KuCoin, ...) implement this interface so the
rest of the app can be exchange-agnostic.

Methods:
    fetch_all_funding_rates()   bulk fetch, returns dict[symbol_unified, FundingRate]
    fetch_ticker(symbol_unified)   live price for a symbol
    place_market_order(...)     place a market order (uses ccxt for auth)
    close_position(...)         close a position (uses ccxt for auth)
    fetch_positions(symbol?)    open positions
    test_credentials(...)       validate API keys
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    """Subclasses must implement read-only methods. Trading is provided
    by the base via ccxt when keys are configured."""

    name: str = "base"
    ccxt_id: str = ""  # filled by subclass

    @abstractmethod
    def fetch_all_funding_rates(self) -> dict[str, FundingRate]:
        """Bulk fetch all USDT-margined perp funding rates on this venue.
        Returns dict keyed by UNIFIED symbol "BASE/USDT:USDT"."""

    @abstractmethod
    def fetch_ticker(self, unified_symbol: str) -> Optional[Ticker]:
        """Live ticker for one symbol (unified)."""

    # ─── Trading (uses ccxt; subclasses may override for native API) ──

    def _ccxt(self):  # lazy import to keep startup fast
        import ccxt

        from .keys_helper import _build_ccxt_config
        cfg = _build_ccxt_config(self.name)
        cls = getattr(ccxt, self.ccxt_id)
        return cls(cfg)

    def _ccxt_with_keys(self, api_key: str, api_secret: str):
        import ccxt

        cfg = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        cls = getattr(ccxt, self.ccxt_id)
        return cls(cfg)

    def place_market_order(
        self,
        unified_symbol: str,
        side: str,
        amount: float,
        *,
        reduce_only: bool = False,
    ) -> dict:
        """Place a market order. Requires API keys configured."""
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if amount is None or float(amount) <= 0:
            raise ValueError("amount must be positive")
        from .symbols import unified_to_native

        raw = unified_to_native(self.name, unified_symbol)
        ex = self._ccxt()
        params = {"reduceOnly": True} if reduce_only else {}
        return ex.create_order(raw, "market", side, float(amount), params=params)

    def fetch_positions(self, unified_symbol: Optional[str] = None) -> list[dict]:
        from .symbols import unified_to_native

        ex = self._ccxt()
        raw = unified_to_native(self.name, unified_symbol) if unified_symbol else None
        try:
            positions = ex.fetch_positions([raw] if raw else None)
        except TypeError:
            positions = ex.fetch_positions(params={"symbol": raw} if raw else None)
        except Exception:
            return []

        def _is_open(p: dict) -> bool:
            for k in ("contracts", "contractSize", "amount", "size"):
                v = p.get(k)
                if v is None:
                    continue
                try:
                    if float(v) != 0:
                        return True
                except (TypeError, ValueError):
                    continue
            return False

        if raw:
            positions = [
                p for p in positions
                if str(p.get("symbol", "")).upper() == raw.upper()
            ]
        return [p for p in positions if _is_open(p)]

    def close_position(
        self, unified_symbol: str, side: str, amount: Optional[float] = None
    ) -> dict:
        """Close position by placing opposite market order."""
        opposite = "sell" if side == "buy" else "buy"
        if amount is None:
            for p in self.fetch_positions(unified_symbol):
                if str(p.get("symbol", "")).upper() == unified_symbol.upper().replace("/", "").replace(":USDT", ""):
                    for k in ("contracts", "contractSize", "amount", "size"):
                        v = p.get(k)
                        if v is not None:
                            try:
                                amount = abs(float(v))
                                break
                            except (TypeError, ValueError):
                                continue
                    if amount:
                        break
        if amount is None or amount <= 0:
            raise ValueError(f"No open position found for {unified_symbol} on {self.name}")
        return self.place_market_order(
            unified_symbol, opposite, float(amount), reduce_only=True
        )

    def test_credentials(self, api_key: str, api_secret: str) -> dict:
        """Verify *api_key*/*api_secret*. Returns dict {success, balance, error}."""
        import ccxt
        try:
            ex = self._ccxt_with_keys(api_key, api_secret)
            bal = ex.fetch_balance()
        except ccxt.AuthenticationError as e:
            return {"success": False, "balance": None, "error": f"AuthenticationError: {e}"}
        except ccxt.PermissionDenied as e:
            return {"success": False, "balance": None, "error": f"PermissionDenied: {e}"}
        except ccxt.NetworkError as e:
            return {"success": False, "balance": None, "error": f"NetworkError: {e}"}
        except ccxt.ExchangeError as e:
            return {"success": False, "balance": None, "error": f"ExchangeError: {e}"}
        except Exception as e:
            return {"success": False, "balance": None, "error": f"{type(e).__name__}: {e}"}

        balance = None
        if isinstance(bal, dict):
            for key in ("total", "free", "used"):
                section = bal.get(key)
                if isinstance(section, dict):
                    if "USDT" in section and section["USDT"] is not None:
                        try:
                            balance = float(section["USDT"])
                            break
                        except (TypeError, ValueError):
                            pass
        return {"success": True, "balance": balance, "error": None}
