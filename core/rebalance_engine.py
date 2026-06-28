"""
Auto Rebalance Engine — FR Bot

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

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from config import (
    REBALANCE_THRESHOLD,
    REBALANCE_PAPER_FEE_PCT,
    REBALANCE_PAPER_DELAY_SEC,
    REBALANCE_CHECK_INTERVAL_SEC,
    REBALANCE_AUTO_TRANSFER,
    PAPER_MODE,
)

log = logging.getLogger("fr-bot.rebalance")


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
            self._execute_live_notify(status)

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

    def _execute_live_notify(self, status: RebalanceStatus):
        """Live mode: log the transfer request. No auto-withdrawal by default."""
        if REBALANCE_AUTO_TRANSFER:
            log.info(
                "[REBALANCE] AUTO TRANSFER ENABLED — %.2f USDT %s → %s",
                status.amount_to_transfer, status.from_exchange, status.to_exchange,
            )
            # Placeholder: actual withdrawal API integration would go here
            # Requires address whitelisting and withdrawal permission
        else:
            log.info(
                "[REBALANCE] Manual transfer needed: %.2f USDT %s → %s",
                status.amount_to_transfer, status.from_exchange, status.to_exchange,
            )
        self._paper_transfer_done_at = 0.0

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

        # Live mode — poll real balances
        if now - self._last_check_time < REBALANCE_CHECK_INTERVAL_SEC:
            return "waiting"

        self._last_check_time = now
        status = self.check_balance()
        if status.is_balanced:
            self._is_rebalancing = False
            log.info("[REBALANCE] Live balances now balanced — done")
            return "done"
        return "waiting"