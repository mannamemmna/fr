"""KuCoin Futures authenticated client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional
import logging

import requests

from core.rate_limiter import get_limiter

BASE_URL = "https://api-futures.kucoin.com"

log = logging.getLogger("kucoin_live")


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
        return {"ok": True, "symbol": self.to_symbol(symbol), "leverage": leverage}

    def open_market(self, symbol: str, side: str, amount_usd: float, leverage: int,
                     *, client_oid: str | None = None) -> dict[str, Any]:
        kc_symbol = self.to_symbol(symbol)
        price = self.get_ticker(kc_symbol)["mark_price"]
        if price <= 0:
            raise RuntimeError(f"KuCoin invalid mark price for {kc_symbol}")
        qty = max(1, round((amount_usd * leverage) / price))
        body = {
            "clientOid": client_oid or f"frbot-{int(time.time()*1000)}",
            "side": "buy" if side.lower() == "buy" else "sell",
            "symbol": kc_symbol,
            "type": "market",
            "size": qty,
            "leverage": str(leverage),
        }
        with get_limiter("kucoin", 10):
            j = self._request("POST", "/api/v1/orders", body=body)
        return {
            "order_id": j.get("data", {}).get("orderId"),
            "symbol": kc_symbol,
            "side": side.lower(),
            "qty": float(qty),
            "requested_qty": float(qty),
            "avg_price": price,
            "raw": j,
        }

    def close_market(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        kc_symbol = self.to_symbol(symbol)
        close_side = "sell" if side.lower() == "buy" else "buy"
        body = {
            "clientOid": f"frbot-close-{int(time.time()*1000)}",
            "side": close_side,
            "symbol": kc_symbol,
            "type": "market",
            "size": max(1, round(qty)),
            "reduceOnly": True,
        }
        with get_limiter("kucoin", 10):
            j = self._request("POST", "/api/v1/orders", body=body)
        return {"order_id": j.get("data", {}).get("orderId"), "symbol": kc_symbol, "side": close_side, "qty": qty, "raw": j}

    def get_order_fill(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Query status fill AKTUAL sebuah order."""
        try:
            with get_limiter("kucoin", 10):
                j = self._request("GET", f"/api/v1/orders/{order_id}")
            data = j.get("data", {})
            filled_qty = float(data.get("filledSize", 0) or 0)
            deal_value = float(data.get("filledValue", 0) or 0)
            avg_price = (deal_value / filled_qty) if filled_qty > 0 else 0.0
            fee = float(data.get("fee", 0) or 0)   # ← BARU
            is_active = bool(data.get("isActive", False))
            cancel_exist = bool(data.get("cancelExist", False))

            if cancel_exist and filled_qty == 0:
                status = "cancelled"
            elif not is_active and filled_qty > 0:
                status = "filled"
            elif is_active and filled_qty > 0:
                status = "partial"
            elif is_active:
                status = "open"
            else:
                status = "unknown"
            return {"status": status, "filled_qty": filled_qty, "avg_price": avg_price, "fee": fee, "raw": data}
        except Exception:
            log.exception("KuCoin get_order_fill failed for order %s", order_id)
            return {"status": "unknown", "filled_qty": 0.0, "avg_price": 0.0, "fee": 0.0}

    def get_order_by_client_oid(self, client_oid: str) -> Optional[dict[str, Any]]:
        """Cari order lewat clientOid. Dipakai recovery saat retry response hilang."""
        try:
            with get_limiter("kucoin", 10):
                j = self._request("GET", f"/api/v1/orders/byClientOid/{client_oid}")
            data = j.get("data")
            if not data:
                return None
            qty_val = float(data.get("size", 0) or 0)
            return {
                "order_id": data.get("id"),
                "symbol": data.get("symbol"),
                "side": data.get("side", "").lower(),
                "qty": qty_val,
                "requested_qty": qty_val,
                "avg_price": float(data.get("price", 0) or 0),
                "raw": data,
            }
        except Exception:
            log.exception("KuCoin get_order_by_client_oid failed for %s", client_oid)
            return None

    def get_position_size(self, symbol: str, side: str) -> float:
        kc_symbol = self.to_symbol(symbol)
        try:
            with get_limiter("kucoin", 10):
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

    # ─── Withdrawal methods (for live rebalance) ────────────────────────

    CHAIN_CODE_MAP_KUCOIN = {
        "TRON": "trx",
        "BSC": "bsc",
        "BASE": "base",
        "ARBITRUM": "arbitrum",
    }

    def get_withdraw_quota(self, currency: str = "USDT", chain: str | None = None) -> dict:
        params = {"currency": currency}
        if chain:
            params["chain"] = chain
        j = self._request("GET", "/api/v1/withdrawals/quotas", params)
        return j.get("data", {})

    def withdraw(self, coin: str, network: str, address: str, amount: float,
                  *, client_id: str, memo: str | None = None) -> dict[str, Any]:
        chain = self.CHAIN_CODE_MAP_KUCOIN.get(network.upper())
        if not chain:
            raise ValueError(f"Unsupported network for KuCoin withdraw: {network}")
        body = {
            "currency": coin,
            "address": address,
            "amount": str(amount),
            "chain": chain,
            "withdrawType": "ADDRESS",
            "isInner": False,
            "clientOid": client_id,
        }
        if memo:
            body["memo"] = memo
        with get_limiter("kucoin", 10):
            j = self._request("POST", "/api/v1/withdrawals", body=body)
        return {"withdraw_id": j.get("data", {}).get("withdrawalId"), "raw": j}

    def get_withdrawal_status(self, withdraw_id: str) -> dict[str, Any]:
        with get_limiter("kucoin", 10):
            j = self._request("GET", f"/api/v1/withdrawals/{withdraw_id}")
        data = j.get("data", {})
        raw_status = data.get("status", "")
        status = "complete" if raw_status == "SUCCESS" else \
                 "failed" if raw_status in ("FAILURE",) else "pending"
        return {"status": status, "raw": data}

    # ─── Deposit detection + internal transfer (Main → Futures) ────────

    def get_recent_deposits(self, currency: str = "USDT", limit: int = 20) -> list[dict]:
        """Query recent on-chain deposits landing in the Main Account."""
        j = self._request("GET", "/api/v1/deposit-list", {"currency": currency, "limit": limit})
        return j.get("data", {}).get("items", [])

    def find_deposit_by_amount_and_address(self, currency: str, expected_amount: float,
                                            address: str, since_ts: float) -> Optional[dict]:
        rows = self.get_recent_deposits(currency)
        for row in rows:
            if row.get("address", "").lower() != address.lower():
                continue
            amt = float(row.get("amount", 0) or 0)
            if abs(amt - expected_amount) > max(0.01, expected_amount * 0.001):
                continue
            row_ts = int(row.get("createdAt", 0) or 0) / 1000
            if row_ts and row_ts < since_ts:
                continue
            if row.get("status") == "SUCCESS":
                return row
        return None

    def transfer_main_to_futures(self, currency: str, amount: float, *, client_oid: str) -> dict:
        """Internal transfer: Main Account → Futures Account."""
        body = {
            "clientOid": client_oid,  # idempotency
            "currency": currency,
            "amount": str(amount),
            "from": "main",
            "to": "futures",
        }
        with get_limiter("kucoin", 10):
            j = self._request("POST", "/api/v3/accounts/universal-transfer", body=body)
        return {"transfer_id": j.get("data", {}).get("orderId") or client_oid, "raw": j}
