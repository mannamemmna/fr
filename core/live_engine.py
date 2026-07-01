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
            # If BB order went through but KC failed → auto-close BB leg immediately
            unwind_result = None
            if bb_order and not kc_order:
                try:
                    bb_qty = float(bb_order.get("qty", 0) or 0)
                    if bb_qty > 0:
                        unwind_side = "sell" if side_bybit == "buy" else "buy"
                        unwind_result = self.bybit.close_market(symbol, unwind_side, bb_qty)
                        log.warning("PARTIAL UNWIND: BB leg closed after KC failed for %s", symbol)
                except Exception as unwind_err:
                    log.error("UNWIND FAILED: %s — manually close Bybit %s", unwind_err, symbol)
            elif kc_order and not bb_order:
                try:
                    kc_qty = float(kc_order.get("qty", 0) or 0)
                    if kc_qty > 0:
                        unwind_side = "sell" if side_kucoin == "buy" else "buy"
                        unwind_result = self.kucoin.close_market(symbol, unwind_side, kc_qty)
                        log.warning("PARTIAL UNWIND: KC leg closed after BB failed for %s", symbol)
                except Exception as unwind_err:
                    log.error("UNWIND FAILED: %s — manually close KuCoin %s", unwind_err, symbol)

            result = {
                "task_id": task_id,
                "status": "failed_partial" if bb_order or kc_order else "failed",
                "critical": bool(bb_order or kc_order),
                "errors": [str(e)],
                "bybit_order": bb_order,
                "kucoin_order": kc_order,
                "unwind": bool(unwind_result),
                "message": "One leg opened then auto-unwound." if unwind_result else
                           "If status=failed_partial, one leg may be live. Manually check exchange and hedge/close immediately.",
                "started_at": started_at,
                "finished_at": _utcnow_iso(),
            }
            self._log_execution({"type": "open_failed", **result})
            return result

        position_size = amount_usd * leverage
        avg_price = max(float(bb_order.get("avg_price") or 0), float(kc_order.get("avg_price") or 0), 0.0001)
        # Use actual filled qty from exchange response
        actual_qty_bb = float(bb_order.get("qty", 0) or 0)
        actual_qty_kc = float(kc_order.get("qty", 0) or 0)
        position = {
            "id": task_id,
            "symbol": symbol.upper(),
            "side_bybit": side_bybit,
            "side_kucoin": side_kucoin,
            "amount_usd": amount_usd,
            "position_size": round(position_size, 2),
            "leverage": leverage,
            "quantity": actual_qty_bb or round(position_size / avg_price, 8),
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

        # Extract fill prices from exchange response
        bb_fill = float(bb.get("raw", {}).get("result", {}).get("avgPrice", 0) or 0)
        if not bb_fill:
            bb_fill = float(bb.get("avg_price", 0) or 0)
        kc_fill = float(kc.get("raw", {}).get("data", {}).get("dealPrice", 0) or 0)
        if not kc_fill:
            kc_fill = float(kc.get("avg_price", 0) or 0)

        entry_bb = float(pos.get("entry_price_bybit", 0) or 0)
        entry_kc = float(pos.get("entry_price_kucoin", 0) or 0)
        qty_bb = float(pos.get("qty_bybit", 0) or 0)
        qty_kc = float(pos.get("qty_kucoin", 0) or 0)
        amount_usd = float(pos.get("amount_usd", 0) or 0)
        leverage = int(pos.get("leverage", 1) or 1)

        # Price PnL: if side=sell, PnL = qty * (entry - exit); if side=buy, PnL = qty * (exit - entry)
        if pos["side_bybit"] == "sell":
            bb_pnl = qty_bb * (entry_bb - bb_fill)
        else:
            bb_pnl = qty_bb * (bb_fill - entry_bb)
        if pos["side_kucoin"] == "sell":
            kc_pnl = qty_kc * (entry_kc - kc_fill)
        else:
            kc_pnl = qty_kc * (kc_fill - entry_kc)

        realized_pnl = bb_pnl + kc_pnl

        with self._lock:
            pos["status"] = "closed"
            pos["exit_time"] = _utcnow_iso()
            pos["close_bybit"] = bb
            pos["close_kucoin"] = kc
            pos["exit_price_bybit"] = bb_fill
            pos["exit_price_kucoin"] = kc_fill
            pos["realized_pnl"] = round(realized_pnl, 2)
            self._realized_pnl += realized_pnl
            self._positions = [p for p in self._positions if p["id"] != pos["id"]]
            self._closed_positions.append(pos)
            self._save_portfolio()
        result = {"ok": True, "position_id": pos["id"], "symbol": pos["symbol"], "realized_pnl": round(realized_pnl, 2), "fees": 0.0, "balance_after": self.get_balance(), "amount_usd": amount_usd, "leverage": leverage, "position_size": amount_usd * leverage}
        self._log_execution({"type": "close", **result, "finished_at": _utcnow_iso()})
        return result

    def close_all_positions(self) -> List[Dict[str, Any]]:
        return [self.close_position(p["id"]) for p in self.get_open_positions()]

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p for p in self._positions if p.get("status") == "open"]

    def get_position_status(self, symbol: str, side_bb: str, side_kc: str) -> Dict[str, str]:
        """Check if both legs still have open positions on the exchanges.
        
        Returns {"bybit": "open"|"closed", "kucoin": "open"|"closed"}.
        -1.0 from get_position_size means API error (treated conservatively as 'unknown').
        """
        bb_size = self.bybit.get_position_size(symbol, side_bb)
        kc_size = self.kucoin.get_position_size(symbol, side_kc)
        
        status = {}
        if bb_size < 0:
            status["bybit"] = "unknown"
        elif bb_size == 0:
            status["bybit"] = "closed"
        else:
            status["bybit"] = "open"
        
        if kc_size < 0:
            status["kucoin"] = "unknown"
        elif kc_size == 0:
            status["kucoin"] = "closed"
        else:
            status["kucoin"] = "open"
        
        return status

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
