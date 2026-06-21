"""Bybit V5 authenticated futures client.

Used only when PAPER_MODE=false and LIVE_CONFIRM=true.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any

import requests

BASE_URL = "https://api.bybit.com"


def _fmt_qty(qty: float, step: float = 0.001) -> str:
    d = Decimal(str(qty))
    s = Decimal(str(step))
    return str((d / s).to_integral_value(rounding=ROUND_DOWN) * s).rstrip("0").rstrip(".")


class BybitLiveClient:
    def __init__(self, api_key: str, api_secret: str, *, base_url: str = BASE_URL, session=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.recv_window = "5000"

    def _sign(self, ts: str, payload: str) -> str:
        raw = ts + self.api_key + self.recv_window + payload
        return hmac.new(self.api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def _headers(self, ts: str, signature: str) -> dict[str, str]:
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self.recv_window,
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict[str, Any]:
        ts = str(int(time.time() * 1000))
        if method.upper() == "GET":
            from urllib.parse import urlencode
            payload = urlencode(params or {})
            url = f"{self.base_url}{path}" + (f"?{payload}" if payload else "")
            data = None
        else:
            payload = json.dumps(body or {}, separators=(",", ":"))
            url = f"{self.base_url}{path}"
            data = payload
        sig = self._sign(ts, payload)
        r = self.session.request(method, url, headers=self._headers(ts, sig), data=data, timeout=10)
        r.raise_for_status()
        j = r.json()
        if str(j.get("retCode")) != "0":
            raise RuntimeError(f"Bybit API error: {j}")
        return j

    @staticmethod
    def to_symbol(symbol: str) -> str:
        s = symbol.upper().replace("/USDT", "").replace(":USDT", "")
        return s if s.endswith("USDT") else f"{s}USDT"

    def get_usdt_balance(self) -> float:
        j = self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
        coins = j.get("result", {}).get("list", [{}])[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                return float(c.get("walletBalance") or c.get("equity") or 0)
        return 0.0

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        bybit_symbol = self.to_symbol(symbol)
        j = self._request("GET", "/v5/market/tickers", {"category": "linear", "symbol": bybit_symbol})
        item = j.get("result", {}).get("list", [{}])[0]
        return {"symbol": bybit_symbol, "mark_price": float(item.get("markPrice") or item.get("lastPrice") or 0)}

    def set_leverage(self, symbol: str, leverage: int):
        bybit_symbol = self.to_symbol(symbol)
        body = {"category": "linear", "symbol": bybit_symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)}
        try:
            return self._request("POST", "/v5/position/set-leverage", body=body)
        except RuntimeError as e:
            # Bybit returns error if leverage already set; don't fail trade for that.
            if "110043" in str(e) or "leverage not modified" in str(e).lower():
                return {"ok": True, "ignored": str(e)}
            raise

    def open_market(self, symbol: str, side: str, amount_usd: float, leverage: int) -> dict[str, Any]:
        bybit_symbol = self.to_symbol(symbol)
        self.set_leverage(bybit_symbol, leverage)
        price = self.get_ticker(bybit_symbol)["mark_price"]
        if price <= 0:
            raise RuntimeError(f"Bybit invalid mark price for {bybit_symbol}")
        qty = _fmt_qty((amount_usd * leverage) / price)
        body = {
            "category": "linear",
            "symbol": bybit_symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderType": "Market",
            "qty": qty,
        }
        j = self._request("POST", "/v5/order/create", body=body)
        return {"order_id": j.get("result", {}).get("orderId"), "symbol": bybit_symbol, "side": side.lower(), "qty": float(qty), "avg_price": price, "raw": j}

    def close_market(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        bybit_symbol = self.to_symbol(symbol)
        close_side = "Sell" if side.lower() == "buy" else "Buy"
        body = {"category": "linear", "symbol": bybit_symbol, "side": close_side, "orderType": "Market", "qty": _fmt_qty(qty), "reduceOnly": True}
        j = self._request("POST", "/v5/order/create", body=body)
        return {"order_id": j.get("result", {}).get("orderId"), "symbol": bybit_symbol, "side": close_side.lower(), "qty": qty, "raw": j}
