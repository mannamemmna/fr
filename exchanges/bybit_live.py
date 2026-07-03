"""Bybit V5 authenticated futures client.

Used only when PAPER_MODE=false and LIVE_CONFIRM=true.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional
import logging

import requests

BASE_URL = "https://api.bybit.com"

log = logging.getLogger("bybit_live")


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
        self._instrument_cache: dict[str, tuple[float, float]] = {}
        self._instrument_lock = threading.Lock()

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

    def _get_instrument_step(self, symbol: str) -> tuple[float, float]:
        """Ambil (qty_step, min_order_qty) untuk symbol, di-cache in-memory."""
        bybit_symbol = self.to_symbol(symbol)
        with self._instrument_lock:
            cached = self._instrument_cache.get(bybit_symbol)
            if cached:
                return cached
        try:
            r = self.session.request(
                "GET", f"{self.base_url}/v5/market/instruments-info",
                params={"category": "linear", "symbol": bybit_symbol}, timeout=10,
            )
            r.raise_for_status()
            j = r.json()
            rows = j.get("result", {}).get("list", [])
            if not rows:
                raise RuntimeError(f"No instrument info for {bybit_symbol}")
            lot = rows[0].get("lotSizeFilter", {})
            step = float(lot.get("qtyStep", 0.001) or 0.001)
            min_qty = float(lot.get("minOrderQty", step) or step)
        except Exception:
            log.exception("Bybit instrument-info lookup gagal untuk %s, pakai fallback step 0.001", bybit_symbol)
            step, min_qty = 0.001, 0.001
        with self._instrument_lock:
            self._instrument_cache[bybit_symbol] = (step, min_qty)
        return step, min_qty

    def set_leverage(self, symbol: str, leverage: int):
        bybit_symbol = self.to_symbol(symbol)
        body = {"category": "linear", "symbol": bybit_symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)}
        try:
            return self._request("POST", "/v5/position/set-leverage", body=body)
        except RuntimeError as e:
            if "110043" in str(e) or "leverage not modified" in str(e).lower():
                return {"ok": True, "ignored": str(e)}
            raise

    def open_market(self, symbol: str, side: str, amount_usd: float, leverage: int,
                     *, order_link_id: str | None = None) -> dict[str, Any]:
        bybit_symbol = self.to_symbol(symbol)
        self.set_leverage(bybit_symbol, leverage)
        price = self.get_ticker(bybit_symbol)["mark_price"]
        if price <= 0:
            raise RuntimeError(f"Bybit invalid mark price for {bybit_symbol}")
        step, min_qty = self._get_instrument_step(symbol)
        qty = _fmt_qty((amount_usd * leverage) / price, step)
        if float(qty) < min_qty:
            raise RuntimeError(
                f"Computed qty {qty} di bawah minOrderQty Bybit {min_qty} untuk {bybit_symbol} "
                f"— naikkan amount_usd atau leverage."
            )
        body = {
            "category": "linear",
            "symbol": bybit_symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderType": "Market",
            "qty": qty,
        }
        if order_link_id:
            body["orderLinkId"] = order_link_id
        j = self._request("POST", "/v5/order/create", body=body)
        return {
            "order_id": j.get("result", {}).get("orderId"),
            "symbol": bybit_symbol,
            "side": side.lower(),
            "qty": float(qty),
            "requested_qty": float(qty),
            "avg_price": price,
            "raw": j,
        }

    def close_market(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        bybit_symbol = self.to_symbol(symbol)
        step, _ = self._get_instrument_step(symbol)
        close_side = "Sell" if side.lower() == "buy" else "Buy"
        body = {
            "category": "linear", "symbol": bybit_symbol, "side": close_side,
            "orderType": "Market", "qty": _fmt_qty(qty, step), "reduceOnly": True,
        }
        j = self._request("POST", "/v5/order/create", body=body)
        return {"order_id": j.get("result", {}).get("orderId"), "symbol": bybit_symbol,
                "side": close_side.lower(), "qty": qty, "raw": j}

    def get_order_fill(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Query status fill AKTUAL sebuah order."""
        bybit_symbol = self.to_symbol(symbol)
        try:
            j = self._request("GET", "/v5/order/realtime", {
                "category": "linear", "symbol": bybit_symbol, "orderId": order_id,
            })
            rows = j.get("result", {}).get("list", [])
            if not rows:
                j2 = self._request("GET", "/v5/order/history", {
                    "category": "linear", "symbol": bybit_symbol, "orderId": order_id,
                })
                rows = j2.get("result", {}).get("list", [])
            if not rows:
                return {"status": "unknown", "filled_qty": 0.0, "avg_price": 0.0, "fee": 0.0}

            row = rows[0]
            filled_qty = float(row.get("cumExecQty", 0) or 0)
            avg_price = float(row.get("avgPrice", 0) or 0)
            fee = float(row.get("cumExecFee", 0) or 0)
            order_status = row.get("orderStatus", "")
            if order_status == "Filled":
                status = "filled"
            elif order_status == "PartiallyFilled":
                status = "partial"
            elif order_status == "New":
                status = "open"
            elif order_status in ("Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"):
                status = "cancelled" if filled_qty == 0 else "partial"
            else:
                status = "unknown"
            return {"status": status, "filled_qty": filled_qty, "avg_price": avg_price, "fee": fee, "raw": row}
        except Exception:
            log.exception("Bybit get_order_fill failed for order %s", order_id)
            return {"status": "unknown", "filled_qty": 0.0, "avg_price": 0.0, "fee": 0.0}

    def get_order_by_link_id(self, symbol: str, order_link_id: str) -> Optional[dict[str, Any]]:
        bybit_symbol = self.to_symbol(symbol)
        try:
            j = self._request("GET", "/v5/order/realtime", {
                "category": "linear", "symbol": bybit_symbol, "orderLinkId": order_link_id,
            })
            rows = j.get("result", {}).get("list", [])
            if not rows:
                j2 = self._request("GET", "/v5/order/history", {
                    "category": "linear", "symbol": bybit_symbol, "orderLinkId": order_link_id,
                })
                rows = j2.get("result", {}).get("list", [])
            if not rows:
                return None
            row = rows[0]
            qty = float(row.get("qty", 0) or 0)
            return {
                "order_id": row.get("orderId"),
                "symbol": bybit_symbol,
                "side": row.get("side", "").lower(),
                "qty": qty,
                "requested_qty": qty,
                "avg_price": float(row.get("avgPrice", 0) or 0),
                "raw": row,
            }
        except Exception:
            log.exception("Bybit get_order_by_link_id failed for %s", order_link_id)
            return None

    def get_position_size(self, symbol: str, side: str) -> float:
        bybit_symbol = self.to_symbol(symbol)
        try:
            j = self._request("GET", "/v5/position/list", {"category": "linear", "symbol": bybit_symbol})
            positions = j.get("result", {}).get("list", [])
            for p in positions:
                p_side = "sell" if p.get("side") == "Sell" else "buy"
                if p_side == side.lower():
                    return abs(float(p.get("size", 0) or 0))
            return 0.0
        except Exception:
            log.exception("Bybit get_position_size failed")
            return -1.0
