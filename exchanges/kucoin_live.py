"""KuCoin Futures authenticated client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any
import logging

import requests

BASE_URL = "https://api-futures.kucoin.com"

log = logging.getLogger("kucoin_live")


def _fmt_qty(qty: float, step: float = 1.0) -> str:
    d = Decimal(str(qty))
    s = Decimal(str(step))
    return str((d / s).to_integral_value(rounding=ROUND_DOWN) * s).rstrip("0").rstrip(".") or "1"


class KuCoinLiveClient:
    def __init__(self, api_key: str, api_secret: str, passphrase: str, *, base_url: str = BASE_URL, session=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def _sign(self, ts: str, method: str, endpoint: str, body: str = "") -> str:
        raw = f"{ts}{method.upper()}{endpoint}{body}"
        return base64.b64encode(hmac.new(self.api_secret.encode(), raw.encode(), hashlib.sha256).digest()).decode()

    def _signed_passphrase(self) -> str:
        return base64.b64encode(hmac.new(self.api_secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()).decode()

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict[str, Any]:
        from urllib.parse import urlencode
        query = urlencode(params or {})
        endpoint = path + (f"?{query}" if query else "")
        data = json.dumps(body or {}, separators=(",", ":")) if method.upper() != "GET" else ""
        ts = str(int(time.time() * 1000))
        headers = {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": self._sign(ts, method, endpoint, data),
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": self._signed_passphrase(),
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }
        r = self.session.request(method, f"{self.base_url}{endpoint}", headers=headers, data=data or None, timeout=10)
        r.raise_for_status()
        j = r.json()
        if str(j.get("code")) != "200000":
            raise RuntimeError(f"KuCoin API error: {j}")
        return j

    @staticmethod
    def to_symbol(symbol: str) -> str:
        s = symbol.upper().replace("/USDT", "").replace(":USDT", "")
        if s == "BTC":
            s = "XBT"
        if s.endswith("USDTM"):
            return s
        if s.endswith("USDT"):
            s = s[:-4]
        return f"{s}USDTM"

    def get_usdt_balance(self) -> float:
        j = self._request("GET", "/api/v1/account-overview", {"currency": "USDT"})
        data = j.get("data", {})
        return float(data.get("availableBalance") or data.get("accountEquity") or 0)

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        kc_symbol = self.to_symbol(symbol)
        j = self._request("GET", "/api/v1/ticker", {"symbol": kc_symbol})
        data = j.get("data", {})
        return {"symbol": kc_symbol, "mark_price": float(data.get("price") or data.get("bestAskPrice") or data.get("bestBidPrice") or 0)}

    def set_leverage(self, symbol: str, leverage: int):
        # KuCoin futures leverage is sent per order; no separate leverage endpoint needed for basic market orders.
        return {"ok": True, "symbol": self.to_symbol(symbol), "leverage": leverage}

    def open_market(self, symbol: str, side: str, amount_usd: float, leverage: int) -> dict[str, Any]:
        kc_symbol = self.to_symbol(symbol)
        price = self.get_ticker(kc_symbol)["mark_price"]
        if price <= 0:
            raise RuntimeError(f"KuCoin invalid mark price for {kc_symbol}")
        # KuCoin futures order size is contracts. This approximation is safe for small USDT-margined contracts;
        # production users should tune contract multipliers per symbol.
        qty = max(1, int((amount_usd * leverage) / price))
        body = {
            "clientOid": f"frbot-{int(time.time()*1000)}",
            "side": "buy" if side.lower() == "buy" else "sell",
            "symbol": kc_symbol,
            "type": "market",
            "size": qty,
            "leverage": str(leverage),
        }
        j = self._request("POST", "/api/v1/orders", body=body)
        return {"order_id": j.get("data", {}).get("orderId"), "symbol": kc_symbol, "side": side.lower(), "qty": float(qty), "avg_price": price, "raw": j}

    def close_market(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        kc_symbol = self.to_symbol(symbol)
        close_side = "sell" if side.lower() == "buy" else "buy"
        body = {
            "clientOid": f"frbot-close-{int(time.time()*1000)}",
            "side": close_side,
            "symbol": kc_symbol,
            "type": "market",
            "size": max(1, int(qty)),
            "reduceOnly": True,
        }
        j = self._request("POST", "/api/v1/orders", body=body)
        return {"order_id": j.get("data", {}).get("orderId"), "symbol": kc_symbol, "side": close_side, "qty": qty, "raw": j}

    def get_position_size(self, symbol: str, side: str) -> float:
        """Return current position size. 0.0 means no position (liquidated)."""
        kc_symbol = self.to_symbol(symbol)
        try:
            j = self._request("GET", "/api/v1/positions", {"symbol": kc_symbol})
            data = j.get("data", {})
            current_qty = abs(float(data.get("currentQty", 0) or 0))
            if current_qty == 0:
                return 0.0
            pos_side = "sell" if float(data.get("currentQty", 0) or 0) < 0 else "buy"
            if pos_side == side.lower():
                return current_qty
            return 0.0
        except Exception:
            log.exception("KuCoin get_position_size failed")
            return -1.0
