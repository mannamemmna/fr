"""Live trading engine for real Bybit × KuCoin funding arbitrage.

Safety rules:
- Requires LIVE_CONFIRM=true (or live_confirm=True in tests) before creating.
- Validates both exchange balances before opening.
- Opens two market legs; if second leg fails, emits a critical error result with
  first-leg order details so the operator can manually hedge/close immediately.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from config import (
    DATA_DIR,
    LIVE_CONFIRM,
    BYBIT_API_KEY,
    BYBIT_API_SECRET,
    KUCOIN_API_KEY,
    KUCOIN_API_SECRET,
    KUCOIN_API_PASSPHRASE,
)
from exchanges.bybit_live import BybitLiveClient
from exchanges.kucoin_live import KuCoinLiveClient

log = logging.getLogger("live_engine")

LIVE_PORTFOLIO_FILE = os.path.join(DATA_DIR, "live_portfolio.json")
LIVE_EXEC_LOG_FILE = os.path.join(DATA_DIR, "live_execution_log.jsonl")


class LiveModeLockedError(RuntimeError):
    pass


class MissingLiveCredentialsError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveEngine:
    """Real exchange engine with the same public API as PaperEngine."""

    def __init__(
        self,
        *,
        live_confirm: bool | None = None,
        bybit_key: str | None = None,
        bybit_secret: str | None = None,
        kucoin_key: str | None = None,
        kucoin_secret: str | None = None,
        kucoin_passphrase: str | None = None,
        bybit_client=None,
        kucoin_client=None,
    ):
        self._lock = threading.RLock()
        self._positions: List[Dict[str, Any]] = []
        self._closed_positions: List[Dict[str, Any]] = []
        self._realized_pnl = 0.0
        self._total_fees = 0.0
        self._load_portfolio()

        confirmed = LIVE_CONFIRM if live_confirm is None else live_confirm
        if not confirmed:
            raise LiveModeLockedError("LIVE MODE LOCKED: set LIVE_CONFIRM=true to allow real exchange orders")

        if bybit_client and kucoin_client:
            self.bybit = bybit_client
            self.kucoin = kucoin_client
            return

        bybit_key = bybit_key if bybit_key is not None else BYBIT_API_KEY
        bybit_secret = bybit_secret if bybit_secret is not None else BYBIT_API_SECRET
        kucoin_key = kucoin_key if kucoin_key is not None else KUCOIN_API_KEY
        kucoin_secret = kucoin_secret if kucoin_secret is not None else KUCOIN_API_SECRET
        kucoin_passphrase = kucoin_passphrase if kucoin_passphrase is not None else KUCOIN_API_PASSPHRASE

        missing = []
        if not bybit_key: missing.append("BYBIT_API_KEY")
        if not bybit_secret: missing.append("BYBIT_API_SECRET")
        if not kucoin_key: missing.append("KUCOIN_API_KEY")
        if not kucoin_secret: missing.append("KUCOIN_API_SECRET")
        if not kucoin_passphrase: missing.append("KUCOIN_API_PASSPHRASE")
        if missing:
            raise MissingLiveCredentialsError("Missing live credentials: " + ", ".join(missing))

        self.bybit = BybitLiveClient(bybit_key, bybit_secret)
        self.kucoin = KuCoinLiveClient(kucoin_key, kucoin_secret, kucoin_passphrase)

    def _load_portfolio(self):
        if not os.path.exists(LIVE_PORTFOLIO_FILE):
            return
        try:
            with open(LIVE_PORTFOLIO_FILE) as f:
                data = json.load(f)
            self._positions = data.get("positions", [])
            self._closed_positions = data.get("closed_positions", [])
            self._realized_pnl = float(data.get("realized_pnl", 0))
            self._total_fees = float(data.get("total_fees", 0))
        except Exception:
            log.exception("Failed to load live portfolio")

    def _save_portfolio(self):
        data = {
            "positions": self._positions,
            "closed_positions": self._closed_positions,
            "realized_pnl": self._realized_pnl,
            "total_fees": self._total_fees,
            "saved_at": _utcnow_iso(),
        }
        tmp = LIVE_PORTFOLIO_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, LIVE_PORTFOLIO_FILE)

    def _log_execution(self, entry: dict):
        with open(LIVE_EXEC_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def get_balance(self) -> float:
        # Conservative: minimum available USDT across both exchanges.
        return min(self.bybit.get_usdt_balance(), self.kucoin.get_usdt_balance())

    def execute_instant(self, symbol: str, amount_usd: float, side_bybit: str, side_kucoin: str, leverage: int = 2) -> Dict[str, Any]:
        task_id = str(uuid.uuid4())
        started_at = _utcnow_iso()
        side_bybit = side_bybit.lower()
        side_kucoin = side_kucoin.lower()
        errors = []
        if side_bybit not in ("buy", "sell"):
            errors.append(f"invalid side_bybit: {side_bybit}")
        if side_kucoin not in ("buy", "sell"):
            errors.append(f"invalid side_kucoin: {side_kucoin}")
        if amount_usd <= 0:
            errors.append("amount_usd must be positive")
        if errors:
            return {"task_id": task_id, "status": "failed", "errors": errors}

        bb_bal = self.bybit.get_usdt_balance()
        kc_bal = self.kucoin.get_usdt_balance()
        if bb_bal < amount_usd or kc_bal < amount_usd:
            return {"task_id": task_id, "status": "failed", "errors": [f"insufficient live balance: Bybit ${bb_bal:.2f}, KuCoin ${kc_bal:.2f}, need ${amount_usd:.2f} each"]}

        bb_order = None
        kc_order = None
        try:
            bb_order = self.bybit.open_market(symbol, side_bybit, amount_usd, leverage)
            kc_order = self.kucoin.open_market(symbol, side_kucoin, amount_usd, leverage)
        except Exception as e:
            result = {
                "task_id": task_id,
                "status": "failed_partial" if bb_order or kc_order else "failed",
                "critical": bool(bb_order or kc_order),
                "errors": [str(e)],
                "bybit_order": bb_order,
                "kucoin_order": kc_order,
                "message": "If status=failed_partial, one leg may be live. Manually check exchange and hedge/close immediately.",
                "started_at": started_at,
                "finished_at": _utcnow_iso(),
            }
            self._log_execution({"type": "open_failed", **result})
            return result

        position_size = amount_usd * leverage
        avg_price = max(float(bb_order.get("avg_price") or 0), float(kc_order.get("avg_price") or 0), 0.0001)
        position = {
            "id": task_id,
            "symbol": symbol.upper(),
            "side_bybit": side_bybit,
            "side_kucoin": side_kucoin,
            "amount_usd": amount_usd,
            "position_size": round(position_size, 2),
            "leverage": leverage,
            "quantity": round(position_size / avg_price, 8),
            "qty_bybit": bb_order.get("qty"),
            "qty_kucoin": kc_order.get("qty"),
            "entry_price_bybit": bb_order.get("avg_price"),
            "entry_price_kucoin": kc_order.get("avg_price"),
            "bybit_order_id": bb_order.get("order_id"),
            "kucoin_order_id": kc_order.get("order_id"),
            "entry_time": started_at,
            "status": "open",
            "paper": False,
        }
        with self._lock:
            self._positions.append(position)
            self._save_portfolio()
        result = {"task_id": task_id, "mode": "live", "symbol": symbol, "amount_usd": amount_usd, "side_bybit": side_bybit, "side_kucoin": side_kucoin, "status": "done", "position": position, "started_at": started_at, "finished_at": _utcnow_iso()}
        self._log_execution({"type": "open", **result})
        return result

    def close_position(self, position_id: str) -> Dict[str, Any]:
        with self._lock:
            pos = next((p for p in self._positions if p.get("id", "").startswith(position_id)), None)
            if not pos:
                return {"ok": False, "error": "position not found", "position_id": position_id}
            pos["status"] = "closing"
            self._save_portfolio()
        try:
            bb = self.bybit.close_market(pos["symbol"], pos["side_bybit"], float(pos.get("qty_bybit") or pos.get("quantity") or 0))
            kc = self.kucoin.close_market(pos["symbol"], pos["side_kucoin"], float(pos.get("qty_kucoin") or pos.get("quantity") or 0))
        except Exception as e:
            pos["status"] = "open"
            self._save_portfolio()
            return {"ok": False, "critical": True, "error": str(e), "position_id": position_id, "message": "Close failed. Check exchanges manually."}

        with self._lock:
            pos["status"] = "closed"
            pos["exit_time"] = _utcnow_iso()
            pos["close_bybit"] = bb
            pos["close_kucoin"] = kc
            self._positions = [p for p in self._positions if p["id"] != pos["id"]]
            self._closed_positions.append(pos)
            self._save_portfolio()
        result = {"ok": True, "position_id": pos["id"], "symbol": pos["symbol"], "realized_pnl": 0.0, "fees": 0.0, "balance_after": self.get_balance(), "amount_usd": pos.get("amount_usd", 0), "leverage": pos.get("leverage", 1), "position_size": pos.get("position_size", 0)}
        self._log_execution({"type": "close", **result, "finished_at": _utcnow_iso()})
        return result

    def close_all_positions(self) -> List[Dict[str, Any]]:
        return [self.close_position(p["id"]) for p in self.get_open_positions()]

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p for p in self._positions if p.get("status") == "open"]

    def get_closed_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._closed_positions)

    def get_summary(self) -> dict:
        open_positions = self.get_open_positions()
        bb_bal = self.bybit.get_usdt_balance()
        kc_bal = self.kucoin.get_usdt_balance()
        total_exposure = sum(p.get("position_size", p.get("amount_usd", 0)) for p in open_positions)
        return {
            "paper_mode": False,
            "balance": round(bb_bal + kc_bal, 2),
            "bybit_balance": round(bb_bal, 2),
            "kucoin_balance": round(kc_bal, 2),
            "realized_pnl": round(self._realized_pnl, 2),
            "unrealized_pnl": 0.0,
            "total_pnl": round(self._realized_pnl, 2),
            "total_fees": round(self._total_fees, 2),
            "total_funding_pnl": 0.0,
            "open_positions": len(open_positions),
            "total_exposure": round(total_exposure, 2),
            "closed_positions": len(self._closed_positions),
            "positions": open_positions,
        }
