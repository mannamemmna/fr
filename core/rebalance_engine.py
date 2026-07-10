"""Auto Rebalance Engine — FR Bot

Handles balance synchronisation between Bybit and KuCoin.
Supports both paper (simulated) and live (real API) modes.

Public API:
    RebalanceEngine(engine, paper_mode=True)
    .check_balance()          → RebalanceStatus
    .needs_rebalance()        → bool
    .start_rebalance(status)  → None
    .tick(now)                → "done" | "waiting" | "failed"
    .get_status()             → dict
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from config import (
    REBALANCE_THRESHOLD,
    REBALANCE_PAPER_FEE_PCT,
    REBALANCE_PAPER_DELAY_SEC,
    REBALANCE_CHECK_INTERVAL_SEC,
    REBALANCE_AUTO_TRANSFER,
    PAPER_MODE,
    REBALANCE_LIVE_TRANSFER_ENABLED,
    REBALANCE_LIVE_DRY_RUN,
    REBALANCE_TOKEN,
    REBALANCE_NETWORK,
    REBALANCE_BYBIT_DEPOSIT_ADDRESS,
    REBALANCE_KUCOIN_DEPOSIT_ADDRESS,
    REBALANCE_BYBIT_DEPOSIT_MEMO,
    REBALANCE_KUCOIN_DEPOSIT_MEMO,
    REBALANCE_MIN_TRANSFER_USD,
    REBALANCE_MAX_TRANSFER_USD,
    REBALANCE_WITHDRAW_POLL_INTERVAL_SEC,
    REBALANCE_WITHDRAW_POLL_TIMEOUT_SEC,
    DATA_DIR,
)

log = logging.getLogger("fr-bot.rebalance")

TRANSFER_LOG_FILE = os.path.join(DATA_DIR, "rebalance_transfers.jsonl")


class RebalanceGuardError(RuntimeError):
    """Raised when a live transfer fails a pre-flight safety check."""


def _log_transfer(entry: dict):
    with open(TRANSFER_LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


@dataclass
class RebalanceStatus:
    bybit_balance: float
    kucoin_balance: float
    total: float
    ratio_bybit: float
    ratio_kucoin: float
    is_balanced: bool
    needs_rebalance: bool
    from_exchange: str
    to_exchange: str
    amount_to_transfer: float
    threshold: float


class RebalanceEngine:
    """Auto balance manager — keeps both exchange balances within threshold."""

    def __init__(self, engine, paper_mode: bool = True):
        self._engine = engine
        self._paper_mode = paper_mode
        self._is_rebalancing = False
        self._rebalance_start_time: float = 0.0
        self._rebalance_target: Dict[str, Any] = {}
        self._paper_transfer_done_at: float = 0.0
        self._last_check_time: float = 0.0
        self._enabled = True  # can be toggled via /rebalance

        # Live transfer state
        self._live_withdraw_poll: Optional[Dict[str, Any]] = None
        self._last_check_time: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def toggle(self, state: bool | None = None):
        if state is None:
            self._enabled = not self._enabled
        else:
            self._enabled = state
        log.info("[REBALANCE] Enabled = %s", self._enabled)

    # ── Balance queries ────────────────────────────────────────────────

    def _get_bybit_balance(self) -> float:
        if self._paper_mode:
            return self._engine.get_bybit_balance()
        return float(self._engine.bybit.get_usdt_balance())

    def _get_kucoin_balance(self) -> float:
        if self._paper_mode:
            return self._engine.get_kucoin_balance()
        return float(self._engine.kucoin.get_usdt_balance())

    def check_balance(self) -> RebalanceStatus:
        bb = self._get_bybit_balance()
        kc = self._get_kucoin_balance()
        total = bb + kc
        ratio_bb = bb / total if total > 0 else 0.5
        ratio_kc = kc / total if total > 0 else 0.5
        min_ratio = min(ratio_bb, ratio_kc)
        needs = total > 0 and min_ratio < REBALANCE_THRESHOLD

        target_each = total / 2.0
        amount = abs(bb - target_each)

        from_ex = "bybit" if bb > kc else "kucoin"
        to_ex = "kucoin" if from_ex == "bybit" else "bybit"

        return RebalanceStatus(
            bybit_balance=bb,
            kucoin_balance=kc,
            total=total,
            ratio_bybit=ratio_bb,
            ratio_kucoin=ratio_kc,
            is_balanced=not needs,
            needs_rebalance=needs,
            from_exchange=from_ex,
            to_exchange=to_ex,
            amount_to_transfer=amount,
            threshold=REBALANCE_THRESHOLD,
        )

    def needs_rebalance(self) -> bool:
        return self.check_balance().needs_rebalance

    def get_status(self) -> dict:
        st = self.check_balance()
        return {
            "enabled": self._enabled,
            "is_rebalancing": self._is_rebalancing,
            "bybit_balance": st.bybit_balance,
            "kucoin_balance": st.kucoin_balance,
            "total": st.total,
            "ratio_bybit": round(st.ratio_bybit * 100, 1),
            "ratio_kucoin": round(st.ratio_kucoin * 100, 1),
            "is_balanced": st.is_balanced,
            "threshold": st.threshold,
            "from_exchange": st.from_exchange,
            "to_exchange": st.to_exchange,
            "amount_to_transfer": round(st.amount_to_transfer, 2),
        }

    # ── Rebalance execution ────────────────────────────────────────────

    def start_rebalance(self, status: RebalanceStatus):
        """Initiate rebalance. In paper mode, schedules a simulated transfer."""
        if self._is_rebalancing:
            log.warning("[REBALANCE] Already rebalancing, ignoring start_rebalance")
            return

        self._is_rebalancing = True
        self._rebalance_start_time = time.time()
        self._rebalance_target = {
            "from": status.from_exchange,
            "to": status.to_exchange,
            "amount": status.amount_to_transfer,
        }

        if status.amount_to_transfer < 1.0:
            log.info("[REBALANCE] Amount %.2f < 1.0 USD, skipping", status.amount_to_transfer)
            self._is_rebalancing = False
            return

        if self._paper_mode:
            self._execute_paper_transfer(status)
        else:
            self._execute_live_transfer(status)

    def _execute_paper_transfer(self, status: RebalanceStatus):
        """Paper mode: simulate a transfer with fee + delay."""
        amount = status.amount_to_transfer
        fee = amount * REBALANCE_PAPER_FEE_PCT
        net = amount - fee

        with self._engine._lock:
            if status.from_exchange == "bybit":
                self._engine._balance_bybit -= amount
                self._engine._balance_kucoin += net
            else:
                self._engine._balance_kucoin -= amount
                self._engine._balance_bybit += net

        self._paper_transfer_done_at = time.time() + REBALANCE_PAPER_DELAY_SEC

        log.info(
            "[REBALANCE] Paper transfer %.4f USDT %s → %s (fee: %.4f, net: %.4f)",
            amount, status.from_exchange, status.to_exchange, fee, net,
        )

        log.debug(
            "[REBALANCE] Balances after transfer: Bybit=%.2f KuCoin=%.2f",
            self._engine._balance_bybit,
            self._engine._balance_kucoin,
        )

    def _execute_live_transfer(self, status: RebalanceStatus):
        """Real CEX-to-CEX withdrawal. Replaces the old _execute_live_notify no-op."""
        amount = round(status.amount_to_transfer, 2)

        if not REBALANCE_LIVE_TRANSFER_ENABLED:
            log.info("[REBALANCE] Live transfer disabled (REBALANCE_LIVE_TRANSFER_ENABLED=false) — "
                      "manual transfer needed: %.2f USDT %s → %s", amount, status.from_exchange, status.to_exchange)
            self._paper_transfer_done_at = 0.0
            self._is_rebalancing = False
            return

        # ── Pre-flight guards ──────────────────────────────────────────
        try:
            self._guard_transfer(status, amount)
        except RebalanceGuardError as e:
            log.error("[REBALANCE] Guard rejected transfer: %s", e)
            self._emit_and_stop(f"🚫 Auto transfer diblokir guard: {e}")
            return

        dest_address = (REBALANCE_KUCOIN_DEPOSIT_ADDRESS if status.to_exchange == "kucoin"
                         else REBALANCE_BYBIT_DEPOSIT_ADDRESS)
        dest_memo = (REBALANCE_KUCOIN_DEPOSIT_MEMO if status.to_exchange == "kucoin"
                     else REBALANCE_BYBIT_DEPOSIT_MEMO)
        source_client = self._engine.bybit if status.from_exchange == "bybit" else self._engine.kucoin

        client_id = f"frbot-reb-{uuid.uuid4().hex[:16]}"
        record = {
            "type": "withdraw_initiated",
            "client_id": client_id,
            "from": status.from_exchange, "to": status.to_exchange,
            "token": REBALANCE_TOKEN, "network": REBALANCE_NETWORK,
            "amount": amount, "address": dest_address,
            "dry_run": REBALANCE_LIVE_DRY_RUN,
            "ts": time.time(),
        }
        _log_transfer(record)  # persist BEFORE calling the API

        if REBALANCE_LIVE_DRY_RUN:
            log.warning("[REBALANCE][DRY RUN] Would withdraw %.2f %s via %s from %s to %s (%s)",
                        amount, REBALANCE_TOKEN, REBALANCE_NETWORK, status.from_exchange,
                        status.to_exchange, dest_address)
            self._emit_and_stop(
                f"🧪 DRY RUN — akan transfer `{amount:.2f} {REBALANCE_TOKEN}` via `{REBALANCE_NETWORK}` "
                f"dari *{status.from_exchange}* ke *{status.to_exchange}*.\nTidak ada dana yang bergerak."
            )
            return

        try:
            result = source_client.withdraw(
                REBALANCE_TOKEN, REBALANCE_NETWORK, dest_address, amount,
                client_id=client_id, memo=dest_memo or None,
            )
        except Exception as e:
            log.error("[REBALANCE] withdraw() call failed: %s", e)
            _log_transfer({**record, "type": "withdraw_call_failed", "error": str(e)})
            self._emit_and_stop(f"❌ Withdrawal API call gagal: {e}")
            return

        withdraw_id = result.get("withdraw_id")
        _log_transfer({**record, "type": "withdraw_submitted", "withdraw_id": withdraw_id})
        self._live_withdraw_poll = {
            "client": source_client, "withdraw_id": withdraw_id,
            "deadline": time.time() + REBALANCE_WITHDRAW_POLL_TIMEOUT_SEC,
            "record": record,
        }
        log.info("[REBALANCE] Withdrawal submitted: id=%s amount=%.2f %s → %s",
                  withdraw_id, amount, status.from_exchange, status.to_exchange)

    def _guard_transfer(self, status: RebalanceStatus, amount: float):
        if amount < REBALANCE_MIN_TRANSFER_USD:
            raise RebalanceGuardError(f"amount {amount} < min {REBALANCE_MIN_TRANSFER_USD}")
        if amount > REBALANCE_MAX_TRANSFER_USD:
            raise RebalanceGuardError(f"amount {amount} > max {REBALANCE_MAX_TRANSFER_USD} — raise cap deliberately if intended")
        dest = REBALANCE_KUCOIN_DEPOSIT_ADDRESS if status.to_exchange == "kucoin" else REBALANCE_BYBIT_DEPOSIT_ADDRESS
        if not dest:
            raise RebalanceGuardError(f"no deposit address configured for {status.to_exchange}")

    def _emit_and_stop(self, msg: str):
        log.warning("[REBALANCE] %s", msg)
        self._is_rebalancing = False
        # engine caller (automation_engine) reads status via get_status()/events;
        # AutomationEngine._start_rebalance already emits its own event, this is
        # for the failure/dry-run path specifically — wire into event_callback
        # if RebalanceEngine gets one, or surface via get_status()["last_message"].

    # ── Tick ────────────────────────────────────────────────────────────

    def tick(self, now: float) -> str:
        """Called by AutomationEngine while state == REBALANCING.

        Returns:
            "done"    → rebalance completed, switch to IDLE
            "waiting" → still in progress
            "failed"  → rebalance failed (unlikely in paper mode)
        """
        if not self._is_rebalancing:
            return "done"

        if self._paper_mode:
            # Simulated delay
            if now >= self._paper_transfer_done_at and self._paper_transfer_done_at > 0:
                self._is_rebalancing = False
                log.info("[REBALANCE] Paper transfer completed")
                return "done"
            return "waiting"

        # Live mode with an in-flight withdrawal
        poll = getattr(self, "_live_withdraw_poll", None)
        if poll:
            if now - self._last_check_time < REBALANCE_WITHDRAW_POLL_INTERVAL_SEC:
                return "waiting"
            self._last_check_time = now
            try:
                wd_status = poll["client"].get_withdrawal_status(poll["withdraw_id"])
            except Exception:
                log.exception("[REBALANCE] withdrawal status check failed")
                return "waiting"

            if wd_status["status"] == "complete":
                _log_transfer({**poll["record"], "type": "withdraw_complete"})
                self._live_withdraw_poll = None
                self._is_rebalancing = False
                log.info("[REBALANCE] Withdrawal confirmed complete")
                return "done"
            if wd_status["status"] == "failed":
                _log_transfer({**poll["record"], "type": "withdraw_failed", "raw": wd_status.get("raw")})
                self._live_withdraw_poll = None
                self._is_rebalancing = False
                log.error("[REBALANCE] Withdrawal FAILED — manual check required")
                return "failed"
            if now >= poll["deadline"]:
                log.error("[REBALANCE] Withdrawal poll TIMEOUT — status unknown, manual check required")
                return "waiting"  # keep polling; do not silently give up
            return "waiting"

        # No in-flight withdrawal and not paper → fall back to old balance-poll behavior
        # (covers REBALANCE_LIVE_TRANSFER_ENABLED=false path)
        if now - self._last_check_time < REBALANCE_CHECK_INTERVAL_SEC:
            return "waiting"
        self._last_check_time = now
        status = self.check_balance()
        if status.is_balanced:
            self._is_rebalancing = False
            log.info("[REBALANCE] Live balances now balanced — done")
            return "done"
        return "waiting"