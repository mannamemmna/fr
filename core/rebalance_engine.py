"""Rebalance Engine — menjaga delta-neutral position saat harga bergerak.

Pure computation module. Dipanggil dari AutomationEngine._tick_live().

Tiga aksi:
  A1 — Trim: balikkan leg yang lebih besar ke ukuran leg yang lebih kecil
  A2 — Rescale: tutup+buka ulang dengan ukuran aman
  A3 — Emergency: tutup semua, kirim alert CRITICAL
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from config import (
    REBALANCE_DELTA_THRESHOLD,
    MIN_MARGIN_RATIO,
    EMERGENCY_MARGIN,
    REBALANCE_COOLDOWN_SEC,
    PAPER_MODE,
)
from core.market_cache import PriceCache

log = logging.getLogger("fr-bot.rebalance")

DEFAULT_TAKER_FEE = 0.0004


class RebalanceEngine:
    """Pure computation. No I/O. Dipanggil per tick dari state LIVE."""

    def __init__(self, price_cache: PriceCache):
        self._price = price_cache
        self._last_rebalance_ts: float = 0.0
        self._rebalance_log: list[dict] = []
        self._enabled = True

    # ─── Public ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        self._enabled = True
        log.info("Rebalancing ENABLED")

    def disable(self):
        self._enabled = False
        log.info("Rebalancing DISABLED")

    def get_rebalance_log(self, limit: int = 10) -> list[dict]:
        return self._rebalance_log[-limit:]

    def get_last_rebalance_ts(self) -> float:
        return self._last_rebalance_ts

    def get_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "last_rebalance_ts": self._last_rebalance_ts,
            "cooldown_remaining": max(0.0, REBALANCE_COOLDOWN_SEC - (time.time() - self._last_rebalance_ts)),
            "threshold_pct": REBALANCE_DELTA_THRESHOLD,
            "margin_min_pct": MIN_MARGIN_RATIO * 100,
            "margin_emergency_pct": EMERGENCY_MARGIN * 100,
            "cooldown_sec": REBALANCE_COOLDOWN_SEC,
            "log": self._rebalance_log[-5:],
        }

    # ─── Main check ───────────────────────────────────────────────────

    def check_and_rebalance(
        self,
        position: dict,
        paper_engine,
        notify_fn: Callable,
    ) -> Optional[dict]:
        """Cek & lakukan rebalancing jika perlu. Kembalikan result dict atau None."""
        if not self._enabled:
            return None

        # Cooldown
        if time.time() - self._last_rebalance_ts < REBALANCE_COOLDOWN_SEC:
            return None

        symbol = position["symbol"]
        bb_price = self._price.get_price("bybit", symbol)
        kc_price = self._price.get_price("kucoin", symbol)

        if bb_price is None or kc_price is None:
            log.debug("REBALANCE skip %s: harga tidak tersedia di cache", symbol)
            return None

        drift = self._compute_drift(position, bb_price, kc_price)
        delta_pct = drift["delta_pct"]

        # Check margin ratios
        margin_bb = drift["margin_ratio_bb"]
        margin_kc = drift["margin_ratio_kc"]
        min_margin = min(margin_bb, margin_kc)

        # Urut prioritas: Emergency > Margin Danger > Delta Drift

        # A3 — Emergency Close
        if min_margin < EMERGENCY_MARGIN:
            result = self._apply_rebalance(
                "emergency_close", position, paper_engine, notify_fn, drift
            )
            self._post_rebalance(position, drift, result, "emergency")
            return result

        # A2 — Full Rescale
        if min_margin < MIN_MARGIN_RATIO:
            result = self._apply_rebalance(
                "rescale_full", position, paper_engine, notify_fn, drift
            )
            self._post_rebalance(position, drift, result, "margin_danger")
            return result

        # A1 — Trim larger leg
        if delta_pct > REBALANCE_DELTA_THRESHOLD:
            result = self._apply_rebalance(
                "trim_larger_leg", position, paper_engine, notify_fn, drift
            )
            self._post_rebalance(position, drift, result, "delta_drift")
            return result

        return None

    # ─── Compute ──────────────────────────────────────────────────────

    def _compute_drift(self, position: dict, bb_price: float, kc_price: float) -> dict:
        qty_bb = float(position.get("qty_bybit", 0) or position.get("quantity", 0))
        qty_kc = float(position.get("qty_kucoin", 0) or position.get("quantity", 0))

        bb_notional = qty_bb * bb_price
        kc_notional = qty_kc * kc_price
        avg_notional = (bb_notional + kc_notional) / 2
        delta_pct = abs(bb_notional - kc_notional) / max(avg_notional, 0.0001) * 100.0

        # Margin ratio: (initial_margin - unrealized_loss) / notional
        side_bb = position.get("side_bybit", "").lower()
        side_kc = position.get("side_kucoin", "").lower()
        entry_bb = float(position.get("entry_price_bybit", 0))
        entry_kc = float(position.get("entry_price_kucoin", 0))
        amount_usd = float(position.get("amount_usd", 0))
        leverage = int(position.get("leverage", 1))
        initial_margin = amount_usd / 2  # approximasi margin per leg

        # Unrealized PnL per leg
        if side_bb == "buy":
            upnl_bb = qty_bb * (bb_price - entry_bb)
        else:
            upnl_bb = qty_bb * (entry_bb - bb_price)

        if side_kc == "buy":
            upnl_kc = qty_kc * (kc_price - entry_kc)
        else:
            upnl_kc = qty_kc * (entry_kc - kc_price)

        margin_ratio_bb = max(0.0, (initial_margin - upnl_bb)) / max(bb_notional, 0.0001)
        margin_ratio_kc = max(0.0, (initial_margin - upnl_kc)) / max(kc_notional, 0.0001)

        return {
            "bb_notional": bb_notional,
            "kc_notional": kc_notional,
            "avg_notional": avg_notional,
            "delta_pct": delta_pct,
            "bb_price": bb_price,
            "kc_price": kc_price,
            "margin_ratio_bb": margin_ratio_bb,
            "margin_ratio_kc": margin_ratio_kc,
            "qty_bb": qty_bb,
            "qty_kc": qty_kc,
        }

    # ─── Apply ────────────────────────────────────────────────────────

    def _apply_rebalance(
        self,
        action: str,
        position: dict,
        paper_engine,
        notify_fn: Callable,
        drift: dict,
    ) -> dict:
        symbol = position["symbol"]
        pos_id = position["id"]

        if action == "emergency_close":
            return self._apply_emergency(position, paper_engine, notify_fn)

        if action == "rescale_full":
            return self._apply_rescale(position, paper_engine, notify_fn, drift)

        if action == "trim_larger_leg":
            return self._apply_trim(position, paper_engine, notify_fn, drift)

        return {"ok": False, "error": f"unknown action: {action}"}

    def _apply_trim(self, position, paper_engine, notify_fn, drift):
        """A1 — Kurangi notional leg yang lebih besar."""
        symbol = position["symbol"]
        pos_id = position["id"]

        # Tentuin leg mana yang lebih besar
        if drift["bb_notional"] > drift["kc_notional"]:
            larger_exchange = "bybit"
            target_notional = drift["kc_notional"]
            excess_notional = drift["bb_notional"] - target_notional
            price = drift["bb_price"]
        else:
            larger_exchange = "kucoin"
            target_notional = drift["bb_notional"]
            excess_notional = drift["kc_notional"] - target_notional
            price = drift["kc_price"]

        if excess_notional <= 0.001:
            return {"ok": False, "action": "trim_larger_leg", "reason": "no_excess"}

        excess_qty = excess_notional / max(price, 0.0001)
        fee = excess_notional * DEFAULT_TAKER_FEE

        result = paper_engine.partial_close_leg(pos_id, larger_exchange, excess_qty)
        if result.get("ok"):
            self._last_rebalance_ts = time.time()

            message = (
                f"⚖️ AUTO REBALANCE — TRIM LEG\n"
                f"Pair: {symbol}\n"
                f"Action: Trim {larger_exchange} (larger leg)\n"
                f"Excess qty: {excess_qty:.4f} → trimmed\n\n"
                f"Before: BB ${drift['bb_notional']:.2f} / KC ${drift['kc_notional']:.2f} (drift {drift['delta_pct']:.1f}%)\n"
                f"After:  BB ${target_notional:.2f} / KC ${target_notional:.2f} (drift 0.0%)\n\n"
                f"Fee dibayar: ${fee:.2f}"
            )
            notify_fn("rebalance", message)

        return {
            "ok": True,
            "action": "trim_larger_leg",
            "exchange": larger_exchange,
            "excess_qty": excess_qty,
            "fee": fee,
            "result": result,
        }

    def _apply_rescale(self, position, paper_engine, notify_fn, drift):
        """A2 — Tutup penuh, buka ulang dengan ukuran lebih aman."""
        symbol = position["symbol"]
        pos_id = position["id"]

        close_result = paper_engine.close_position(pos_id)
        if not close_result.get("ok"):
            return {"ok": False, "action": "rescale_full", "error": close_result.get("error", "close failed")}

        safe_margin = min(position["amount_usd"], paper_engine.get_bybit_balance(), paper_engine.get_kucoin_balance()) * 0.7
        if safe_margin < 10:
            notify_fn(
                "rebalance",
                f"⚠️ REBALANCE — RESCALE FAILED\n{symbol}: saldo terlalu kecil untuk re-entry\n"
                f"BB balance: ${paper_engine.get_bybit_balance():.2f}\n"
                f"KC balance: ${paper_engine.get_kucoin_balance():.2f}",
            )
            return {"ok": False, "action": "rescale_full", "error": "insufficient_balance"}

        entry_result = paper_engine.execute_instant(
            symbol, safe_margin, position["side_bybit"], position["side_kucoin"], position["leverage"]
        )
        self._last_rebalance_ts = time.time()

        message = (
            f"⚖️ AUTO REBALANCE — RESCALE FULL\n"
            f"Pair: {symbol}\n"
            f"Closed: ${position['amount_usd']:.0f} × {position['leverage']}x\n"
            f"Re-entered: ${safe_margin:.0f} × {position['leverage']}x\n\n"
            f"Margin BB: {drift['margin_ratio_bb']*100:.1f}% → safe\n"
            f"Margin KC: {drift['margin_ratio_kc']*100:.1f}% → safe"
        )
        notify_fn("rebalance", message)

        return {"ok": True, "action": "rescale_full", "close_result": close_result, "entry_result": entry_result}

    def _apply_emergency(self, position, paper_engine, notify_fn):
        """A3 — Tutup semua posisi."""
        symbol = position["symbol"]
        results = paper_engine.close_all_positions()
        self._last_rebalance_ts = time.time()

        message = (
            f"🚨 *EMERGENCY REBALANCE* 🚨\n"
            f"All positions closed due to margin emergency!\n"
            f"Trigger: margin < {EMERGENCY_MARGIN*100:.0f}%\n"
            f"Balance: ${paper_engine.get_balance():.2f}"
        )
        notify_fn("rebalance", message)

        return {"ok": True, "action": "emergency_close", "results": results}

    def _post_rebalance(self, position, drift, result, trigger):
        """Log hasil rebalance ke memory log."""
        entry = {
            "ts": time.time(),
            "position_id": position["id"],
            "symbol": position["symbol"],
            "action": result.get("action", "?"),
            "trigger": trigger,
            "drift_before": drift["delta_pct"],
            "drift_after": 0.0,
            "margin_ratio_before": min(drift["margin_ratio_bb"], drift["margin_ratio_kc"]),
            "margin_ratio_after": 0.0,
            "qty_before_bb": drift["qty_bb"],
            "qty_after_bb": drift["qty_bb"],
            "qty_before_kc": drift["qty_kc"],
            "qty_after_kc": drift["qty_kc"],
            "ok": result.get("ok", False),
        }
        self._rebalance_log.append(entry)
        if len(self._rebalance_log) > 100:
            self._rebalance_log = self._rebalance_log[-50:]

        # Also log to SQLite
        try:
            from core.db import get_db
            db = get_db()
            db.log_rebalance(
                position_id=position["id"],
                symbol=position["symbol"],
                action=result.get("action", "?"),
                trigger=trigger,
                drift_before=drift["delta_pct"],
                drift_after=0.0,
                margin_ratio_before=min(drift["margin_ratio_bb"], drift["margin_ratio_kc"]),
                margin_ratio_after=0.0,
                qty_before_bb=drift["qty_bb"],
                qty_after_bb=drift["qty_bb"],
                qty_before_kc=drift["qty_kc"],
                qty_after_kc=drift["qty_kc"],
                fee_paid=result.get("fee", 0),
                paper=PAPER_MODE,
            )
        except Exception:
            log.warning("Failed to log rebalance to DB", exc_info=True)