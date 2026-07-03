"""Live trading engine for real Bybit × KuCoin funding arbitrage.

Safety rules:
- Requires LIVE_CONFIRM=true (or live_confirm=True in tests) before creating.
- Validates both exchange balances before opening.
- Retry + idempotency on placement; poll actual fills; reconcile partial fills.
- Fixes unwind double-flip bug (§1.3 of live-hedge-fill-protection-prompt.md).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import (
    DATA_DIR,
    LIVE_CONFIRM,
    BYBIT_API_KEY,
    BYBIT_API_SECRET,
    KUCOIN_API_KEY,
    KUCOIN_API_SECRET,
    KUCOIN_API_PASSPHRASE,
    LIVE_ORDER_PLACEMENT_MAX_RETRIES,
    LIVE_ORDER_PLACEMENT_RETRY_BASE_SEC,
    LIVE_FILL_POLL_INTERVAL_SEC,
    LIVE_FILL_POLL_TIMEOUT_SEC,
    LIVE_PARTIAL_FILL_TOLERANCE_PCT,
    LIVE_PARTIAL_FILL_TOPUP_MAX_ATTEMPTS,
    LIVE_UNREALIZED_PNL_ENABLED,
)
from exchanges.bybit_live import BybitLiveClient
from exchanges.kucoin_live import KuCoinLiveClient
from core.funding_pnl import compute_funding_pnl
from core.scanner import read_opportunities, run_scan

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
        self._total_funding_pnl = 0.0
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
            self._total_funding_pnl = float(data.get("total_funding_pnl", 0))
        except Exception:
            log.exception("Failed to load live portfolio")

    def _save_portfolio(self):
        data = {
            "positions": self._positions,
            "closed_positions": self._closed_positions,
            "realized_pnl": self._realized_pnl,
            "total_fees": self._total_fees,
            "total_funding_pnl": self._total_funding_pnl,
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
        return min(self.bybit.get_usdt_balance(), self.kucoin.get_usdt_balance())

    def _get_opportunity(self, symbol: str) -> Optional[dict]:
        """Ambil snapshot funding rate terkini dari scan terakhir."""
        data = read_opportunities()
        symbol_upper = symbol.upper()
        for opp in data.get("opportunities", []):
            if opp["symbol"].upper() == symbol_upper:
                return opp
        try:
            run_scan()
            data = read_opportunities()
            for opp in data.get("opportunities", []):
                if opp["symbol"].upper() == symbol_upper:
                    return opp
        except Exception:
            log.exception("Gagal refresh scan untuk entry funding rate snapshot %s", symbol)
        return None

    # ── Helpers ────────────────────────────────────────────────────────

    def _place_leg_with_retry(self, client, symbol, side, amount_usd, leverage,
                               idem_key: str, *, is_kucoin: bool) -> Dict[str, Any]:
        last_err = None
        for attempt in range(LIVE_ORDER_PLACEMENT_MAX_RETRIES):
            try:
                if is_kucoin:
                    return client.open_market(symbol, side, amount_usd, leverage, client_oid=idem_key)
                return client.open_market(symbol, side, amount_usd, leverage, order_link_id=idem_key)
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                is_duplicate = "duplicate" in msg or "order_link_id" in msg or "clientoid" in msg
                if is_duplicate:
                    try:
                        existing = (client.get_order_by_client_oid(idem_key) if is_kucoin
                                    else client.get_order_by_link_id(symbol, idem_key))
                        if existing:
                            log.warning(
                                "Leg placement (%s) hit duplicate-ID error but order already "
                                "exists server-side — recovered order %s instead of retrying",
                                "kucoin" if is_kucoin else "bybit", existing.get("order_id"),
                            )
                            return existing
                    except Exception:
                        log.exception("Gagal recovery order setelah duplicate-ID error")

                log.warning("Leg placement attempt %d/%d gagal (%s): %s",
                            attempt + 1, LIVE_ORDER_PLACEMENT_MAX_RETRIES,
                            "kucoin" if is_kucoin else "bybit", e)
                if attempt < LIVE_ORDER_PLACEMENT_MAX_RETRIES - 1:
                    time.sleep(LIVE_ORDER_PLACEMENT_RETRY_BASE_SEC * (2 ** attempt))
        raise last_err  # type: ignore

    def _poll_fill(self, client, exchange_name: str, symbol: str, order_id: str,
                    requested_qty: float) -> Dict[str, Any]:
        deadline = time.time() + LIVE_FILL_POLL_TIMEOUT_SEC
        last = {"status": "unknown", "filled_qty": 0.0, "avg_price": 0.0, "fee": 0.0}
        while True:
            try:
                last = client.get_order_fill(symbol, order_id)
            except Exception:
                log.exception("get_order_fill gagal untuk %s order %s", exchange_name, order_id)
            if last.get("status") in ("filled", "cancelled"):
                break
            if time.time() >= deadline:
                log.warning("%s fill poll timeout untuk order %s (status=%s, filled=%s/%s)",
                            exchange_name, order_id, last.get("status"),
                            last.get("filled_qty"), requested_qty)
                break
            time.sleep(LIVE_FILL_POLL_INTERVAL_SEC)
        last["exchange"] = exchange_name
        last["requested_qty"] = requested_qty
        last["fill_ratio"] = round(min(last.get("filled_qty", 0.0) / requested_qty, 1.0), 6) if requested_qty else 0.0
        return last

    def _reconcile_partial_fill(self, task_id, symbol, side_bybit, side_kucoin,
                                 bb_fill, kc_fill, bb_requested_qty, kc_requested_qty,
                                 leverage) -> Dict[str, Any]:
        actions = []
        bb_qty = bb_fill["filled_qty"]
        kc_qty = kc_fill["filled_qty"]

        for attempt in range(LIVE_PARTIAL_FILL_TOPUP_MAX_ATTEMPTS):
            ratio_bb = bb_qty / bb_requested_qty if bb_requested_qty else 0
            ratio_kc = kc_qty / kc_requested_qty if kc_requested_qty else 0
            if abs(ratio_bb - ratio_kc) <= LIVE_PARTIAL_FILL_TOLERANCE_PCT:
                break

            if ratio_bb < ratio_kc:
                shortfall_qty = max(0.0, bb_requested_qty - bb_qty)
                if shortfall_qty <= 0:
                    break
                price = bb_fill.get("avg_price") or 0.0001
                shortfall_usd = (shortfall_qty * price) / max(leverage, 1)
                try:
                    topup = self.bybit.open_market(symbol, side_bybit, shortfall_usd, leverage,
                                                    order_link_id=f"frbot-{task_id}-bb-top{attempt}")
                    topup_fill = self._poll_fill(self.bybit, "bybit", symbol, topup["order_id"],
                                                  topup.get("requested_qty", shortfall_qty))
                    bb_qty += topup_fill.get("filled_qty", 0.0)
                    actions.append({"exchange": "bybit", "action": "topup", "attempt": attempt,
                                     "requested_qty": shortfall_qty, "filled_qty": topup_fill.get("filled_qty", 0.0)})
                except Exception as e:
                    log.error("Top-up Bybit gagal untuk %s: %s", symbol, e)
                    actions.append({"exchange": "bybit", "action": "topup_failed", "error": str(e)})
                    break
            else:
                shortfall_qty = max(0.0, kc_requested_qty - kc_qty)
                if shortfall_qty <= 0:
                    break
                price = kc_fill.get("avg_price") or 0.0001
                shortfall_usd = (shortfall_qty * price) / max(leverage, 1)
                try:
                    topup = self.kucoin.open_market(symbol, side_kucoin, shortfall_usd, leverage,
                                                     client_oid=f"frbot-{task_id}-kc-top{attempt}")
                    topup_fill = self._poll_fill(self.kucoin, "kucoin", symbol, topup["order_id"],
                                                  topup.get("requested_qty", shortfall_qty))
                    kc_qty += topup_fill.get("filled_qty", 0.0)
                    actions.append({"exchange": "kucoin", "action": "topup", "attempt": attempt,
                                     "requested_qty": shortfall_qty, "filled_qty": topup_fill.get("filled_qty", 0.0)})
                except Exception as e:
                    log.error("Top-up KuCoin gagal untuk %s: %s", symbol, e)
                    actions.append({"exchange": "kucoin", "action": "topup_failed", "error": str(e)})
                    break

        ratio_bb = bb_qty / bb_requested_qty if bb_requested_qty else 0
        ratio_kc = kc_qty / kc_requested_qty if kc_requested_qty else 0
        if abs(ratio_bb - ratio_kc) > LIVE_PARTIAL_FILL_TOLERANCE_PCT:
            if bb_qty > kc_qty:
                excess = bb_qty - kc_qty
                try:
                    self.bybit.close_market(symbol, side_bybit, excess)
                    bb_qty -= excess
                    actions.append({"exchange": "bybit", "action": "downsize", "qty": excess})
                except Exception as e:
                    log.error("Downsize Bybit gagal untuk %s: %s", symbol, e)
                    actions.append({"exchange": "bybit", "action": "downsize_failed", "error": str(e)})
            elif kc_qty > bb_qty:
                excess = kc_qty - bb_qty
                try:
                    self.kucoin.close_market(symbol, side_kucoin, excess)
                    kc_qty -= excess
                    actions.append({"exchange": "kucoin", "action": "downsize", "qty": excess})
                except Exception as e:
                    log.error("Downsize KuCoin gagal untuk %s: %s", symbol, e)
                    actions.append({"exchange": "kucoin", "action": "downsize_failed", "error": str(e)})

        return {"actions": actions, "final_qty_bybit": bb_qty, "final_qty_kucoin": kc_qty}

    # ── Core execute ───────────────────────────────────────────────────

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

        # ── Step 1: place both legs (retry + idempotency) ────────────────
        try:
            bb_order = self._place_leg_with_retry(
                self.bybit, symbol, side_bybit, amount_usd, leverage,
                idem_key=f"frbot-{task_id}-bb", is_kucoin=False,
            )
        except Exception as e:
            result = {
                "task_id": task_id, "status": "failed", "critical": False,
                "errors": [f"bybit leg placement failed after retries: {e}"],
                "started_at": started_at, "finished_at": _utcnow_iso(),
            }
            self._log_execution({"type": "open_failed", **result})
            return result

        try:
            kc_order = self._place_leg_with_retry(
                self.kucoin, symbol, side_kucoin, amount_usd, leverage,
                idem_key=f"frbot-{task_id}-kc", is_kucoin=True,
            )
        except Exception as e:
            unwind_result = None
            try:
                bb_qty = float(bb_order.get("qty", 0) or 0)
                if bb_qty > 0:
                    unwind_result = self.bybit.close_market(symbol, side_bybit, bb_qty)
                    log.warning("UNWIND: Bybit leg closed after KuCoin failed for %s", symbol)
            except Exception as unwind_err:
                log.error("UNWIND FAILED: %s — manually close Bybit %s NOW", unwind_err, symbol)

            result = {
                "task_id": task_id,
                "status": "failed_unwound" if unwind_result else "failed_partial",
                "critical": not bool(unwind_result),
                "errors": [f"kucoin leg placement failed after retries: {e}"],
                "bybit_order": bb_order,
                "kucoin_order": None,
                "unwind": bool(unwind_result),
                "message": "Bybit leg opened then auto-unwound after KuCoin failed." if unwind_result else
                           "CRITICAL: Bybit leg is live and unwind FAILED. Manually close Bybit position NOW.",
                "started_at": started_at,
                "finished_at": _utcnow_iso(),
            }
            self._log_execution({"type": "open_failed", **result})
            return result

        # ── Step 2: verify actual fills on both legs ──────────────────────
        bb_requested_qty = float(bb_order.get("requested_qty", bb_order.get("qty", 0)) or 0)
        kc_requested_qty = float(kc_order.get("requested_qty", kc_order.get("qty", 0)) or 0)

        bb_fill = self._poll_fill(self.bybit, "bybit", symbol, bb_order["order_id"], bb_requested_qty)
        kc_fill = self._poll_fill(self.kucoin, "kucoin", symbol, kc_order["order_id"], kc_requested_qty)

        # ── Step 3: hard failure — satu leg tidak terisi ──────────────────
        if bb_fill["filled_qty"] <= 0 or kc_fill["filled_qty"] <= 0:
            unwind_results = {}
            if bb_fill["filled_qty"] > 0:
                try:
                    unwind_results["bybit"] = self.bybit.close_market(symbol, side_bybit, bb_fill["filled_qty"])
                except Exception as e:
                    log.error("UNWIND FAILED (bybit): %s — manually close %s NOW", e, symbol)
            if kc_fill["filled_qty"] > 0:
                try:
                    unwind_results["kucoin"] = self.kucoin.close_market(symbol, side_kucoin, kc_fill["filled_qty"])
                except Exception as e:
                    log.error("UNWIND FAILED (kucoin): %s — manually close %s NOW", e, symbol)

            result = {
                "task_id": task_id,
                "status": "failed_unwound" if unwind_results else "failed",
                "critical": False,
                "errors": [
                    f"fill verification failed — bybit filled {bb_fill['filled_qty']}/{bb_requested_qty}, "
                    f"kucoin filled {kc_fill['filled_qty']}/{kc_requested_qty} within "
                    f"{LIVE_FILL_POLL_TIMEOUT_SEC}s"
                ],
                "bybit_fill": bb_fill,
                "kucoin_fill": kc_fill,
                "unwind": unwind_results,
                "started_at": started_at,
                "finished_at": _utcnow_iso(),
            }
            self._log_execution({"type": "open_failed", **result})
            return result

        # ── Step 4: partial-fill reconciliation ──────────────────────────
        reconciliation = None
        ratio_bb = bb_fill["filled_qty"] / bb_requested_qty if bb_requested_qty else 1.0
        ratio_kc = kc_fill["filled_qty"] / kc_requested_qty if kc_requested_qty else 1.0
        if abs(ratio_bb - ratio_kc) > LIVE_PARTIAL_FILL_TOLERANCE_PCT:
            log.warning(
                "PARTIAL FILL detected for %s: bybit=%.1f%% kucoin=%.1f%% — reconciling",
                symbol, ratio_bb * 100, ratio_kc * 100,
            )
            reconciliation = self._reconcile_partial_fill(
                task_id, symbol, side_bybit, side_kucoin,
                bb_fill, kc_fill, bb_requested_qty, kc_requested_qty, leverage,
            )
            bb_fill["filled_qty"] = reconciliation["final_qty_bybit"]
            kc_fill["filled_qty"] = reconciliation["final_qty_kucoin"]

        actual_qty_bb = bb_fill["filled_qty"]
        actual_qty_kc = kc_fill["filled_qty"]
        entry_price_bb = bb_fill.get("avg_price") or bb_order.get("avg_price")
        entry_price_kc = kc_fill.get("avg_price") or kc_order.get("avg_price")
        position_size = amount_usd * leverage

        # Ambil funding rate snapshot dari scan untuk entry
        opp = self._get_opportunity(symbol) or {}
        entry_fee_bb = round(bb_fill.get("fee", 0.0), 8)
        entry_fee_kc = round(kc_fill.get("fee", 0.0), 8)

        position = {
            "id": task_id,
            "symbol": symbol.upper(),
            "side_bybit": side_bybit,
            "side_kucoin": side_kucoin,
            "amount_usd": amount_usd,
            "position_size": round(position_size, 2),
            "leverage": leverage,
            "quantity": actual_qty_bb,
            "qty_bybit": actual_qty_bb,
            "qty_kucoin": actual_qty_kc,
            "entry_price_bybit": entry_price_bb,
            "entry_price_kucoin": entry_price_kc,
            "entry_fee_bybit": entry_fee_bb,
            "entry_fee_kucoin": entry_fee_kc,
            "entry_rate_bybit": opp.get("bybit_rate_pct", 0),
            "entry_rate_kucoin": opp.get("kucoin_rate_pct", 0),
            "bybit_interval_h": opp.get("bybit_interval_h", 8),
            "kucoin_interval_h": opp.get("kucoin_interval_h", 8),
            "bybit_order_id": bb_order.get("order_id"),
            "kucoin_order_id": kc_order.get("order_id"),
            "fill_verification": {"bybit": bb_fill, "kucoin": kc_fill},
            "reconciliation": reconciliation,
            "entry_time": started_at,
            "status": "open",
            "paper": False,
        }
        with self._lock:
            self._positions.append(position)
            self._total_fees += entry_fee_bb + entry_fee_kc
            self._save_portfolio()

        result = {
            "task_id": task_id, "mode": "live", "symbol": symbol, "amount_usd": amount_usd,
            "side_bybit": side_bybit, "side_kucoin": side_kucoin, "status": "done",
            "position": position, "reconciliation": reconciliation,
            "started_at": started_at, "finished_at": _utcnow_iso(),
        }
        self._log_execution({"type": "open", **result})
        return result

    # ── Close ──────────────────────────────────────────────────────────

    def close_position(self, position_id: str) -> Dict[str, Any]:
        with self._lock:
            pos = next((p for p in self._positions if p.get("id", "").startswith(position_id)), None)
            if not pos:
                return {"ok": False, "error": "position not found", "position_id": position_id}
            pos["status"] = "closing"
            self._save_portfolio()

        symbol = pos["symbol"]
        qty_bb = float(pos.get("qty_bybit") or pos.get("quantity") or 0)
        qty_kc = float(pos.get("qty_kucoin") or pos.get("quantity") or 0)

        try:
            bb_order = self.bybit.close_market(symbol, pos["side_bybit"], qty_bb)
            kc_order = self.kucoin.close_market(symbol, pos["side_kucoin"], qty_kc)
        except Exception as e:
            with self._lock:
                pos["status"] = "open"
                self._save_portfolio()
            return {"ok": False, "critical": True, "error": str(e), "position_id": position_id,
                    "message": "Close failed. Check exchanges manually."}

        # Verifikasi fill AKTUAL (harga + fee)
        bb_fill = self._poll_fill(self.bybit, "bybit", symbol, bb_order["order_id"], qty_bb)
        kc_fill = self._poll_fill(self.kucoin, "kucoin", symbol, kc_order["order_id"], qty_kc)

        bb_exit_price = bb_fill.get("avg_price") or 0.0
        kc_exit_price = kc_fill.get("avg_price") or 0.0
        exit_fee_bb = bb_fill.get("fee", 0.0)
        exit_fee_kc = kc_fill.get("fee", 0.0)

        entry_bb = float(pos.get("entry_price_bybit", 0) or 0)
        entry_kc = float(pos.get("entry_price_kucoin", 0) or 0)

        if pos["side_bybit"] == "sell":
            price_pnl_bb = qty_bb * (entry_bb - bb_exit_price)
        else:
            price_pnl_bb = qty_bb * (bb_exit_price - entry_bb)
        if pos["side_kucoin"] == "sell":
            price_pnl_kc = qty_kc * (entry_kc - kc_exit_price)
        else:
            price_pnl_kc = qty_kc * (kc_exit_price - entry_kc)
        total_price_pnl = price_pnl_bb + price_pnl_kc

        entry_fee_bb = float(pos.get("entry_fee_bybit", 0) or 0)
        entry_fee_kc = float(pos.get("entry_fee_kucoin", 0) or 0)
        total_fee = entry_fee_bb + entry_fee_kc + exit_fee_bb + exit_fee_kc

        position_size = pos.get("position_size", pos.get("amount_usd", 0))
        funding = compute_funding_pnl(
            entry_rate_bybit_pct=float(pos.get("entry_rate_bybit", 0) or 0),
            entry_rate_kucoin_pct=float(pos.get("entry_rate_kucoin", 0) or 0),
            bybit_interval_h=int(pos.get("bybit_interval_h", 8) or 8),
            kucoin_interval_h=int(pos.get("kucoin_interval_h", 8) or 8),
            position_size=position_size,
            side_bybit=pos["side_bybit"],
            side_kucoin=pos["side_kucoin"],
            entry_time_iso=pos.get("entry_time", _utcnow_iso()),
        )

        realized_pnl = total_price_pnl + funding["funding_pnl"] - total_fee

        with self._lock:
            pos["status"] = "closed"
            pos["exit_time"] = _utcnow_iso()
            pos["exit_price_bybit"] = bb_exit_price
            pos["exit_price_kucoin"] = kc_exit_price
            pos["exit_fee_bybit"] = round(exit_fee_bb, 8)
            pos["exit_fee_kucoin"] = round(exit_fee_kc, 8)
            pos["total_price_pnl"] = round(total_price_pnl, 8)
            pos["funding_pnl"] = round(funding["funding_pnl"], 8)
            pos["fr_paid"] = round(funding["fr_paid"], 8)
            pos["fr_received"] = round(funding["fr_received"], 8)
            pos["total_fee"] = round(total_fee, 8)
            pos["realized_pnl"] = round(realized_pnl, 8)
            self._realized_pnl += realized_pnl
            self._total_fees += exit_fee_bb + exit_fee_kc
            self._total_funding_pnl += funding["funding_pnl"]
            self._positions = [p for p in self._positions if p["id"] != pos["id"]]
            self._closed_positions.append(pos)
            self._save_portfolio()

        result = {
            "ok": True, "position_id": pos["id"], "symbol": pos["symbol"],
            "side_bybit": pos.get("side_bybit"), "side_kucoin": pos.get("side_kucoin"),
            "entry_price_bybit": entry_bb, "entry_price_kucoin": entry_kc,
            "exit_price_bybit": bb_exit_price, "exit_price_kucoin": kc_exit_price,
            "price_pnl": round(total_price_pnl, 2),
            "funding_pnl": round(funding["funding_pnl"], 2),
            "fr_paid": round(funding["fr_paid"], 2),
            "fr_received": round(funding["fr_received"], 2),
            "fees": round(total_fee, 2),
            "realized_pnl": round(realized_pnl, 2),
            "balance_after": self.get_balance(),
            "amount_usd": pos.get("amount_usd", 0),
            "leverage": pos.get("leverage", 1),
            "position_size": position_size,
        }
        self._log_execution({"type": "close", **result, "finished_at": _utcnow_iso()})
        return result

    def close_all_positions(self) -> List[Dict[str, Any]]:
        return [self.close_position(p["id"]) for p in self.get_open_positions()]

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p for p in self._positions if p.get("status") == "open"]

    def get_position_status(self, symbol: str, side_bb: str, side_kc: str) -> Dict[str, str]:
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

        unrealized_pnl = 0.0
        if LIVE_UNREALIZED_PNL_ENABLED:
            for pos in open_positions:
                try:
                    bb_mark = self.bybit.get_ticker(pos["symbol"])["mark_price"]
                    kc_mark = self.kucoin.get_ticker(pos["symbol"])["mark_price"]
                except Exception:
                    log.warning("Gagal ambil mark price live untuk unrealized PnL %s", pos["symbol"])
                    continue
                entry_bb = float(pos.get("entry_price_bybit", 0) or 0)
                entry_kc = float(pos.get("entry_price_kucoin", 0) or 0)
                qty_bb = float(pos.get("qty_bybit", 0) or 0)
                qty_kc = float(pos.get("qty_kucoin", 0) or 0)
                if pos["side_bybit"] == "sell":
                    pnl_bb = qty_bb * (entry_bb - bb_mark)
                else:
                    pnl_bb = qty_bb * (bb_mark - entry_bb)
                if pos["side_kucoin"] == "sell":
                    pnl_kc = qty_kc * (entry_kc - kc_mark)
                else:
                    pnl_kc = qty_kc * (kc_mark - entry_kc)
                unrealized_pnl += pnl_bb + pnl_kc

        return {
            "paper_mode": False,
            "balance": round(bb_bal + kc_bal, 2),
            "bybit_balance": round(bb_bal, 2),
            "kucoin_balance": round(kc_bal, 2),
            "realized_pnl": round(self._realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl": round(self._realized_pnl + unrealized_pnl, 2),
            "total_fees": round(self._total_fees, 2),
            "total_funding_pnl": round(self._total_funding_pnl, 2),
            "open_positions": len(open_positions),
            "total_exposure": round(total_exposure, 2),
            "closed_positions": len(self._closed_positions),
            "positions": open_positions,
        }
