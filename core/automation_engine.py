"""Automation Engine — autonomous funding rate arbitrage execution.

State machine that runs in a background thread:
    IDLE     → wait for next funding window
    LOOKING  → find best pair by TOP delta (same-interval priority)
    DELAY    → monitor spread, execute if stable; cancel if reversed
    LIVE     → monitor funding reversal, auto-close on flip

Rules:
- Entry window: AUTO_ENTRY_WINDOW_MIN minutes before funding payment
- Max positions: AUTO_MAX_POSITIONS (default 1)
- Bybit 1H × KuCoin 4H: only enter when Bybit FR > KuCoin
- Same interval (4H+4H, 8H+8H): highest priority
- Auto close on funding reversal
- Delay order: cancel if reversal detected, scan another pair
- Leverage: AUTO_LEVERAGE (default 3x)

All timing / thresholds configurable via config.py / .env.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from config import (
    AUTO_MODE,
    AUTO_LEVERAGE,
    AUTO_BALANCE_PER_LEG,
    AUTO_MAX_POSITIONS,
    AUTO_MONITOR_INTERVAL,
    AUTO_ENTRY_WINDOW_MIN,
    AUTO_DELTA_THRESHOLD,
    AUTO_PREFER_SAME_INTERVAL,
    AUTO_DELAY_CANCEL_FUNDING_DIFF,
    AUTO_DELAY_ENTRY_PRICE_SPREAD,
    AUTO_LIVE_CLOSE_FUNDING_DIFF, AUTO_LIVE_CLOSE_PRICE_SPREAD,
    PAPER_MODE,
    REBALANCE_THRESHOLD,
    REBALANCE_CHECK_INTERVAL_SEC,
    HEDGE_EMERGENCY_OPEN,
    HEDGE_CHECK_INTERVAL_SEC,
    HEDGE_BALANCE_DROP_THRESHOLD,
)
from core.scanner import run_scan, read_opportunities
from core.paper_engine import PaperEngine
from core.rebalance_engine import RebalanceEngine
from core.spread_engine import SpreadEngine
from core.market_cache import get_price_cache, get_funding_cache

log = logging.getLogger("fr-bot.auto")

# ─── State enum ────────────────────────────────────────────────────────────


class State(Enum):
    IDLE = "idle"
    REBALANCING = "rebalancing"
    LOOKING = "looking"
    DELAY = "delay"
    LIVE = "live"


# ─── Delay order record ────────────────────────────────────────────────────


@dataclass
class DelayOrder:
    """A pending arb order monitored for price spread stability before execution."""
    symbol: str
    side_bybit: str
    side_kucoin: str
    amount_usd: float = 100.0
    leverage: int = 3
    entry_price_spread: float = 0.0   # Price spread (Bybit–KuCoin mark) at entry %
    entry_delta: float = 0.0          # Funding rate delta at entry % (positive)
    entry_raw_fr_diff: float = 0.0    # Raw funding diff at entry % (can be negative)
    bybit_rate: float = 0.0
    kucoin_rate: float = 0.0
    bybit_next_ts: int = 0
    kucoin_next_ts: int = 0
    bybit_interval_h: int = 0
    kucoin_interval_h: int = 0
    created_at: float = 0.0
    stable_checks: int = 0
    position_id: Optional[str] = None

    def __post_init__(self):
        if self.created_at == 0:
            self.created_at = time.time()


# ─── Event callback protocol ───────────────────────────────────────────────


@dataclass
class AutoEvent:
    """Event emitted to bot for Telegram notifications."""
    type: str  # 'state_change', 'entry', 'close', 'error', 'cancel', 'scan'
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ─── Automation Engine ─────────────────────────────────────────────────────


def _format_trade_summary(result: dict, symbol: str, entry_spread: float,
                        current_spread: float, entry_delta: float, current_delta: float) -> str:
    """Format a full trade summary message after close."""
    if not result.get("ok"):
        return f"❌ *AUTO CLOSE FAILED* — {symbol}\nError: {result.get('error', 'unknown')}"

    price_pnl = float(result.get("price_pnl", 0) or 0)
    funding_pnl = float(result.get("funding_pnl", 0) or 0)
    fr_paid = float(result.get("fr_paid", 0) or 0)
    fr_received = float(result.get("fr_received", 0) or 0)
    fees = float(result.get("fees", 0) or 0)
    realized_pnl = float(result.get("realized_pnl", 0) or 0)
    entry_price_bb = result.get("entry_price_bybit", "—")
    entry_price_kc = result.get("entry_price_kucoin", "—")
    exit_price_bb = result.get("exit_price_bybit", "—")
    exit_price_kc = result.get("exit_price_kucoin", "—")
    entry_fee_bb = float(result.get("entry_fee_bybit", 0) or 0)
    entry_fee_kc = float(result.get("entry_fee_kucoin", 0) or 0)
    exit_fee_bb = float(result.get("exit_fee_bybit", 0) or 0)
    exit_fee_kc = float(result.get("exit_fee_kucoin", 0) or 0)
    total_fee = entry_fee_bb + entry_fee_kc + exit_fee_bb + exit_fee_kc
    side_bb = result.get("side_bybit", "?").upper()
    side_kc = result.get("side_kucoin", "?").upper()
    amount_usd = float(result.get("amount_usd", 0) or 0)
    leverage = int(result.get("leverage", 1) or 1)
    position_size = float(result.get("position_size", amount_usd * leverage) or 0)

    # PnL emoji
    pnl_emoji = "🟢" if realized_pnl >= 0 else "🔴"

    lines = [
        f"{pnl_emoji} *AUTO CLOSE — TRADE SUMMARY*",
        "",
        f"*Pair:* `{symbol}`",
        f"*Position:* `${amount_usd:.0f}` × {leverage}x = `${position_size:.0f}`",
        f"*Direction:* {side_bb} Bybit / {side_kc} KuCoin",
        f"",
        f"━━━ *FUNDING* ━━━",
        f"Diff FR: `{entry_delta:.4f}%` → `{current_delta:.4f}%`",
        f"",
        f"━━━ *PRICE* ━━━",
        f"Bybit: `{entry_price_bb}` → `{exit_price_bb}`",
        f"KuCoin: `{entry_price_kc}` → `{exit_price_kc}`",
        f"",
        f"━━━ *P&L BREAKDOWN* ━━━",
        f"Price PnL: `{price_pnl:+.2f} USD`",
        f"Funding: `{funding_pnl:+.4f} USD` (terima: `{fr_received:.2f}` | bayar: `{fr_paid:.2f}`)",
        f"Fees: `—{total_fee:.4f} USD`",
        f"│ Bybit: `—{entry_fee_bb:.4f}` (entry) + `—{exit_fee_bb:.4f}` (exit) = `—{entry_fee_bb+exit_fee_bb:.4f}`",
        f"│ KuCoin: `—{entry_fee_kc:.4f}` (entry) + `—{exit_fee_kc:.4f}` (exit) = `—{entry_fee_kc+exit_fee_kc:.4f}`",
        f"",
        f"━━━ *RESULT* ━━━",
        f"Realized PnL: *{realized_pnl:+.4f} USD*",
        f"Balance: `${float(result.get('balance_after', 0) or 0):.2f} USD`",
    ]

    return "\n".join(lines)


class AutomationEngine:
    """Core automation state machine.

    Runs in its own thread. Calls paper_engine for execution.
    Emits events via callback for Telegram notifications.
    """

    def __init__(self, paper_engine: PaperEngine, event_callback=None,
                 spread_engine: Optional[SpreadEngine] = None):
        self._paper = paper_engine
        self._event_callback = event_callback
        self._spread = spread_engine
        self._price = get_price_cache()
        self._funding = get_funding_cache()
        self._state = State.IDLE
        self._enabled = AUTO_MODE
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._notify_chat_id: Optional[str] = None
        self._rebalance_engine: Optional[RebalanceEngine] = None

        # Current state data
        self._delay_orders: List[DelayOrder] = []   # list pending delay orders (multi-position support)
        self._live_order: Optional[DelayOrder] = None  # order yg sudah masuk LIVE
        self._live_position_id: Optional[str] = None
        self._funding_threshold_met: bool = False   # Tahap 1 LIVE: sudah terpenuhi?
        self._last_hedge_check: float = 0.0       # Hedge guard throttle
        self._hedge_triggered: bool = False       # Already emergency-closing
        self._entry_balance_snapshot: dict = {}   # Balance snapshot saat LIVE entry
        self._last_scan: dict = {}
        self._last_log = time.time()
        self._delay_notified: set = set()   # simpan symbol yg sudah dinotifikasi delay

    # ─── Properties ────────────────────────────────────────────────────

    @property
    def state(self) -> State:
        return self._state

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def delay_order(self) -> Optional[DelayOrder]:
        """First pending delay order (backward compat)."""
        return self._delay_orders[0] if self._delay_orders else None

    # ─── Control ───────────────────────────────────────────────────────

    def set_notify_chat(self, chat_id: str):
        """Set chat ID for event notifications."""
        self._notify_chat_id = chat_id
        log.info("Notification target set to chat %s", chat_id)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="auto-engine")
        self._thread.start()
        log.info("Automation engine started (enabled=%s)", self._enabled)

    def enable(self):
        with self._lock:
            self._enabled = True
        self._emit_event("state_change", "🟢 Auto mode ON")
        log.info("Auto mode ENABLED")

    def disable(self):
        with self._lock:
            self._enabled = False
            self._state = State.IDLE
            self._delay_orders.clear()
            self._live_order = None
            self._live_position_id = None
        self._emit_event("state_change", "🔴 Auto mode OFF — all pending orders cancelled")
        log.info("Auto mode DISABLED")

    # ─── Main loop ─────────────────────────────────────────────────────

    def _loop(self):
        """Main automation loop — runs every AUTO_MONITOR_INTERVAL seconds."""
        log.info("Automation loop started (interval=%.1fs)", AUTO_MONITOR_INTERVAL)
        while not self._stop_event.is_set():
            try:
                if self._enabled:
                    self._tick()
            except Exception:
                log.exception("Automation tick failed")
            self._stop_event.wait(AUTO_MONITOR_INTERVAL)

    def _tick(self):
        """One automation cycle."""
        now = time.time()
        state = self._state

        if state == State.IDLE:
            self._tick_idle(now)
        elif state == State.REBALANCING:
            self._tick_rebalancing(now)
        elif state == State.LOOKING:
            self._tick_looking(now)
        elif state == State.DELAY:
            self._tick_delay(now)
        elif state == State.LIVE:
            self._tick_live(now)

    # ─── IDLE ──────────────────────────────────────────────────────────

    def _tick_idle(self, now: float):
        """Wait for upcoming funding window within AUTO_ENTRY_WINDOW_MIN minutes.
        
        Balance check runs first — if needs rebalance and no open positions,
        switch to REBALANCING.
        """
        # ── Cek balance sebelum apapun ──────────────────────────────
        if self._rebalance_engine and self._rebalance_engine.enabled:
            status = self._rebalance_engine.check_balance()
            if status.needs_rebalance:
                open_positions = self._paper.get_open_positions()
                if not open_positions and not self._delay_orders:
                    # Tidak ada posisi & delay order → mulai rebalance
                    self._start_rebalance(status, now)
                    return
                elif open_positions or self._delay_orders:
                    log.debug("IDLE: balance tidak seimbang tapi ada posisi terbuka, tunggu...")
                    return
        # ── Sisa logika IDLE yang sudah ada ─────────────────────────
        scan = self._get_scan()
        if not scan:
            return

        # Find pairs with funding in the next window
        window_sec = AUTO_ENTRY_WINDOW_MIN * 60
        candidates = []
        for opp in scan:
            bb_ts = opp.get("bybit_next_ts", 0) or 0
            kc_ts = opp.get("kucoin_next_ts", 0) or 0
            min_ts = min(bb_ts, kc_ts)
            if min_ts <= 0:
                continue
            time_to_funding = min_ts - now
            if 0 < time_to_funding <= window_sec:
                candidates.append(opp)

        if not candidates:
            # Log once per minute
            if now - self._last_log > 60:
                log.debug("IDLE: no funding within %d min", AUTO_ENTRY_WINDOW_MIN)
                self._last_log = now
            return

        # Sort by time-to-funding (closest first), lalu ambil delta terbaik
        candidates.sort(key=lambda o: min(o.get("bybit_next_ts", 0) or 0, o.get("kucoin_next_ts", 0) or 0))
        next_opp = max(candidates, key=lambda o: o["delta_pct"])
        log.info(
            "IDLE → LOOKING: %d pairs within window. Best is %s (delta %.4f%%)",
            len(candidates),
            next_opp["symbol"],
            next_opp["delta_pct"],
        )
        self._state = State.LOOKING

    # ─── LOOKING ───────────────────────────────────────────────────────

    def _tick_looking(self, now: float):
        """Find the best pair by TOP delta. Apply interval rules."""
        # Check if we're past all funding windows
        if not self._in_funding_window(now):
            self._state = State.IDLE
            log.info("LOOKING → IDLE: no more funding windows")
            return

        # Max positions check
        if AUTO_MAX_POSITIONS > 0:
            open_positions = self._paper.get_open_positions()
            if len(open_positions) >= AUTO_MAX_POSITIONS:
                return  # Already at max, no new entries

        scan = self._get_scan()
        if not scan:
            return

        # Filter: pairs within funding window AND min Diff FR >= AUTO_DELTA_THRESHOLD
        window_sec = AUTO_ENTRY_WINDOW_MIN * 60
        candidates = []
        for opp in scan:
            bb_ts = opp.get("bybit_next_ts", 0) or 0
            kc_ts = opp.get("kucoin_next_ts", 0) or 0
            min_ts = min(bb_ts, kc_ts)
            if min_ts <= 0 or min_ts - now > window_sec:
                continue
            if opp["delta_pct"] < AUTO_DELTA_THRESHOLD:
                continue
            candidates.append(opp)

        if not candidates:
            self._state = State.IDLE
            log.info("LOOKING → IDLE: no candidates meeting criteria")
            return

        # Scoring: same-interval priority + delta
        def _score(opp: dict) -> float:
            bb_iv = opp.get("bybit_interval_h", 0) or 0
            kc_iv = opp.get("kucoin_interval_h", 0) or 0
            same_interval_bonus = 0.5 if (AUTO_PREFER_SAME_INTERVAL and bb_iv == kc_iv) else 0.0
            return opp["delta_pct"] + same_interval_bonus

        candidates.sort(key=_score, reverse=True)

        # Ambil top N sesuai MAX_POSITIONS, kurangi yang sudah punya posisi terbuka
        open_positions = self._paper.get_open_positions()
        open_symbols = {p["symbol"] for p in open_positions}
        already_in_delay = {o.symbol for o in self._delay_orders}
        slots_available = max(0, AUTO_MAX_POSITIONS - len(open_positions) - len(self._delay_orders))

        if slots_available <= 0:
            return

        new_targets = []
        for opp in candidates:
            if opp["symbol"] in open_symbols or opp["symbol"] in already_in_delay:
                continue
            new_targets.append(opp)
            if len(new_targets) >= slots_available:
                break

        if not new_targets:
            self._state = State.IDLE
            log.info("LOOKING → IDLE: semua kandidat sudah punya posisi aktif")
            return

        for best in new_targets:
            bb_iv = best.get("bybit_interval_h", 0) or 0
            kc_iv = best.get("kucoin_interval_h", 0) or 0

            bybit_action = best.get("bybit_action", "—")
            if bybit_action == "—":
                log.info("SKIP %s: flat spread", best["symbol"])
                continue

            side_bb = "sell" if bybit_action == "SHORT" else "buy"
            side_kc = "sell" if best.get("kucoin_action") == "SHORT" else "buy"

            price_spread = self._calculate_price_spread(best, side_bb, side_kc)

            delay_order = DelayOrder(
                symbol=best["symbol"],
                side_bybit=side_bb,
                side_kucoin=side_kc,
                amount_usd=AUTO_BALANCE_PER_LEG,
                leverage=AUTO_LEVERAGE,
                entry_price_spread=price_spread,
                entry_delta=best["delta_pct"],
                entry_raw_fr_diff=best.get("raw_fr_diff", 0),
                bybit_rate=best["bybit_rate_pct"],
                kucoin_rate=best["kucoin_rate_pct"],
                bybit_next_ts=best.get("bybit_next_ts") or 0,
                kucoin_next_ts=best.get("kucoin_next_ts") or 0,
                bybit_interval_h=bb_iv,
                kucoin_interval_h=kc_iv,
            )
            self._delay_orders.append(delay_order)

            bb_ts = best.get("bybit_next_ts", 0)
            kc_ts = best.get("kucoin_next_ts", 0)
            min_ts = min(bb_ts, kc_ts) if bb_ts and kc_ts else (bb_ts or kc_ts)
            funding_in = "N/A"
            if min_ts:
                diff_sec = max(0, min_ts - now)
                funding_in = f"{int(diff_sec // 60)} menit"

            log.info(
                "LOOKING → DELAY: %s  spread=%.4f%%  delta=%.4f%%  %s  BB_iv=%dh  KC_iv=%dh",
                best["symbol"], price_spread, best["delta_pct"], best["direction"], bb_iv, kc_iv,
            )
            self._emit_event(
                "state_change",
                (
                    f"🎯 *TARGET LOCKED*\n"
                    f"Pair: *{best['symbol']}*\n"
                    f"Direction: `{best['direction']}`\n\n"
                    f"📊 *Stats:*\n"
                    f"├ Diff FR: `{best['delta_pct']:.4f}%` (Raw: `{(best.get('raw_fr_diff', 0)/100):+.4f}%`)\n"
                    f"├ Price Spread: `{price_spread:+.4f}%`\n"
                    f"└ Interval: `BB {bb_iv}h / KC {kc_iv}h`\n\n"
                    f"💰 *Position:*\n"
                    f"└ `${AUTO_BALANCE_PER_LEG:.0f}` × `{AUTO_LEVERAGE}x` = `${AUTO_BALANCE_PER_LEG * AUTO_LEVERAGE:.0f}` per leg\n\n"
                    f"⏰ Funding in: `{funding_in}` (WIB)\n"
                    f"⚡ _Evaluating market condition..._"
                ),
            )

        self._state = State.DELAY

    # ─── DELAY ─────────────────────────────────────────────────────────

    def _tick_delay(self, now: float):
        """Monitor delay orders. Entry jika spread <= AUTO_DELAY_ENTRY_PRICE_SPREAD.
        Cancel jika Diff FR drop, lalu scan pair lain dalam window."""
        if not self._delay_orders:
            self._state = State.IDLE
            return

        executed_any = False
        cancelled_any = False

        for order in list(self._delay_orders):
            bb_ts = order.bybit_next_ts
            kc_ts = order.kucoin_next_ts
            min_ts = min(bb_ts, kc_ts) if bb_ts and kc_ts else (bb_ts or kc_ts)
            time_left = max(0, min_ts - now)

            # Window expired → cancel, kembali LOOKING
            if time_left <= 0:
                log.info("DELAY → LOOKING: funding window expired for %s", order.symbol)
                self._emit_event("cancel", f"⏰ Window habis untuk *{order.symbol}* — kembali scan...")
                self._delay_orders.remove(order)
                cancelled_any = True
                continue

            # Ambil data scan terkini untuk pair ini
            scan = self._get_scan()
            current = next((o for o in scan if o["symbol"] == order.symbol), None)
            if not current:
                run_scan()
                fresh = read_opportunities()
                current = next((o for o in fresh.get("opportunities", []) if o["symbol"] == order.symbol), None)
            if not current:
                log.warning("DELAY → LOOKING: %s hilang dari scan", order.symbol)
                self._delay_orders.remove(order)
                if order.symbol in self._delay_notified:
                    self._delay_notified.remove(order.symbol)
                cancelled_any = True
                continue

            # Hitung Price Spread saat ini
            price_spread_now = self._calculate_price_spread(current, order.side_bybit, order.side_kucoin)

            curr_delta = current.get("delta_pct", 0)
            entry_delta = order.entry_delta
            entry_ps = order.entry_price_spread

            # ── CANCEL: Diff FR drop drastis ──────────────────────────────
            if curr_delta <= AUTO_DELAY_CANCEL_FUNDING_DIFF:
                log.info("DELAY cancel %s: Diff FR drop %.4f%% <= %.4f%%",
                         order.symbol, curr_delta, AUTO_DELAY_CANCEL_FUNDING_DIFF)
                self._emit_event(
                    "cancel",
                    f"🚫 *ENTRY CANCELLED* | {order.symbol}\n\n"
                    f"⚠️ *Reason:* Diff FR anjlok\n"
                    f"├ Diff FR: `{entry_delta:.4f}%` ➡️ `{curr_delta:.4f}%` (batas: `{AUTO_DELAY_CANCEL_FUNDING_DIFF}%`)\n"
                    f"└ Price Spread: `{price_spread_now:+.4f}%`\n\n"
                    f"🔄 _Scanning pair lain..._"
                )
                self._delay_orders.remove(order)
                if order.symbol in self._delay_notified:
                    self._delay_notified.remove(order.symbol)
                cancelled_any = True
                continue

            # ── ENTRY: Price Spread <= AUTO_DELAY_ENTRY_PRICE_SPREAD ──────
            if price_spread_now <= AUTO_DELAY_ENTRY_PRICE_SPREAD:
                log.info("DELAY → EXECUTE: %s spread=%.4f%% <= %.4f%%",
                         order.symbol, price_spread_now, AUTO_DELAY_ENTRY_PRICE_SPREAD)
                self._execute_delay_order(order, current, time_left)
                self._delay_orders.remove(order)
                if order.symbol in self._delay_notified:
                    self._delay_notified.remove(order.symbol)
                executed_any = True
            else:
                log.debug("DELAY waiting %s: spread=%.4f%% (target<=%.4f%%)  DiffFR=%.4f%%",
                          order.symbol, price_spread_now, AUTO_DELAY_ENTRY_PRICE_SPREAD, curr_delta)
                
                # Kirim notifikasi CUKUP SEKALI di awal masuk queue DELAY
                if order.symbol not in self._delay_notified:
                    self._delay_notified.add(order.symbol)
                    mins_left = int(time_left // 60)
                    secs_left = int(time_left % 60)
                    self._emit_event(
                        "state_change",
                        f"⏳ *Monitoring DELAY* | {order.symbol}\n\n"
                        f"├ Price Spread: `{price_spread_now:+.4f}%` (target: `<= {AUTO_DELAY_ENTRY_PRICE_SPREAD:.2f}%`)\n"
                        f"├ Diff FR: `{curr_delta:.4f}%`\n"
                        f"└ Funding dalam: `{mins_left}m {secs_left}s`\n\n"
                        f"⚡ _Scanning spread setiap {AUTO_MONITOR_INTERVAL}s..._"
                    )

        # Jika ada yang cancel dan masih dalam window, kembali ke LOOKING untuk cari pair baru
        if cancelled_any and self._in_funding_window(now):
            self._state = State.LOOKING
        elif not self._delay_orders and not executed_any:
            self._state = State.IDLE

    # ─── EXECUTE ────────────────────────────────────────────────────────

    def _execute_delay_order(self, order: DelayOrder, current: dict, time_left: float):
        """Execute the delayed order through paper or live engine."""
        if PAPER_MODE:
            result = self._paper.execute_instant(
                order.symbol,
                order.amount_usd,
                order.side_bybit,
                order.side_kucoin,
                order.leverage,
            )
        else:
            log.error("Live execution not yet implemented")
            self._emit_event("error", "🔴 Live execution not yet implemented")
            self._live_order = None
            self._state = State.IDLE
            return

        if result["status"] == "done":
            pos = result.get("position", {})
            order.position_id = pos.get("id")
            self._live_position_id = order.position_id
            self._live_order = order

            # Snapshot balance untuk hedge guard
            summary = self._paper.get_summary()
            self._entry_balance_snapshot = {
                "bybit": summary.get("bybit_balance", 0),
                "kucoin": summary.get("kucoin_balance", 0),
                "time": time.time(),
            }
            self._last_hedge_check = time.time()
            self._hedge_triggered = False

            mins_left = time_left / 60
            self._state = State.LIVE
            log.info("DELAY → LIVE: %s executed, monitoring reversal", order.symbol)
            self._emit_event(
                "entry",
                (
                    f"✅ *AUTO ENTRY*\n"
                    f"Pair: *{order.symbol}*\n"
                    f"Margin: `${order.amount_usd:.0f}` × `{order.leverage}x` = `${pos.get('position_size', order.amount_usd * order.leverage):.0f}`\n"
                    f"Price spread: `{order.entry_price_spread:+.4f}%` ((Long-Short)/Short)\n"
                    f"Funding: `{(current.get('raw_fr_diff', 0)/100):+.4f}%`  |  Diff FR: `{current.get('delta_pct', 0):.4f}%`\n"
                    f"Direction: `{current.get('direction', '—')}`\n"
                    f"⏰ Funding in: `{mins_left:.0f} menit` (WIB)\n"
                    f"⚡ _Monitoring market every {AUTO_MONITOR_INTERVAL}s…_"
                ),
            )
        else:
            errors = "\n".join(result.get("errors", ["unknown"]))
            self._emit_event("error", f"❌ Auto execution failed: {errors}")
            self._live_order = None
            self._state = State.LOOKING

    # ─── LIVE ───────────────────────────────────────────────────────────

    def _tick_live(self, now: float):
        """Monitor open position for funding reversal."""
        pos_id = self._live_position_id
        if not pos_id:
            self._state = State.IDLE
            return

        # Verify position still open
        positions = self._paper.get_open_positions()
        pos = next((p for p in positions if p.get("id") == pos_id), None)
        if not pos:
            log.info("LIVE → IDLE: position %s closed (manual?)", pos_id[:12])
            self._emit_event("state_change", f"📭 Position closed — back to IDLE")
            self._live_position_id = None
            self._live_order = None
            self._funding_threshold_met = False
            self._hedge_triggered = False
            self._entry_balance_snapshot.clear()
            self._state = State.IDLE
            return

        # ── HEDGE INTEGRITY GUARD ───────────────────────────────────────
        # Cek apakah salah satu leg sudah mati (margin call / manual close / likuidasi)
        if HEDGE_EMERGENCY_OPEN and not self._hedge_triggered:
            now = time.time()
            if now - self._last_hedge_check >= HEDGE_CHECK_INTERVAL_SEC:
                self._last_hedge_check = now
                emergency = False
                reason = ""

                if PAPER_MODE:
                    # Paper: cek legs_status di position
                    legs = pos.get("legs_status", {})
                    bb_open = legs.get("bybit") == "open"
                    kc_open = legs.get("kucoin") == "open"
                    if bb_open and not kc_open:
                        emergency = True
                        reason = "KuCoin leg closed, Bybit still open"
                    elif not bb_open and kc_open:
                        emergency = True
                        reason = "Bybit leg closed, KuCoin still open"
                else:
                    # Live: cek via exchange API (1 call/exchange per interval)
                    try:
                        side_bb = pos.get("side_bybit", "").lower()
                        side_kc = pos.get("side_kucoin", "").lower()
                        live_eng = getattr(self, "_live_engine", None)
                        if live_eng:
                            status = live_eng.get_position_status(pos["symbol"], side_bb, side_kc)
                            if status.get("bybit") == "closed" and status.get("kucoin") == "open":
                                emergency = True
                                reason = "Bybit position closed (margin call?), KuCoin still open"
                            elif status.get("kucoin") == "closed" and status.get("bybit") == "open":
                                emergency = True
                                reason = "KuCoin position closed (margin call?), Bybit still open"
                    except Exception:
                        log.warning("LIVE hedge check failed (API error), skipping this interval")

                if emergency:
                    self._hedge_triggered = True
                    log.warning("HEDGE EMERGENCY: %s — %s", pos["symbol"], reason)
                    self._emit_event(
                        "state_change",
                        f"🚨 *HEDGE EMERGENCY* | {pos['symbol']}\n\n"
                        f"{reason}\n\n"
                        f"⚡ _Emergency close — menutup leg satunya..._"
                    )
                    # Close the full position (remaining leg gets closed too)
                    result = self._paper.close_position(pos_id)
                    if result.get("ok"):
                        self._emit_event(
                            "close",
                            f"🚨 *EMERGENCY CLOSE* | {pos['symbol']}\n\n"
                            f"Posisi ditutup darurat karena ada satu leg yang hilang.\n"
                            f"Realized PnL: `{result.get('realized_pnl', 0):+.2f} USD`"
                        )
                    else:
                        self._emit_event("error", f"❌ Emergency close failed: {result.get('error', 'unknown')}")
                    self._live_position_id = None
                    self._live_order = None
                    self._funding_threshold_met = False
                    self._hedge_triggered = False
                    self._state = State.IDLE
                    return

        # Get current scan for this symbol
        symbol = pos["symbol"]
        scan = self._get_scan()
        current = None
        for opp in scan:
            if opp["symbol"] == symbol:
                current = opp
                break

        if not current:
            return  # No data yet, keep monitoring

        # 1. Hitung Price Spread: ((P_Long - P_Short) / P_Short * 100)
        side_bb = pos.get("side_bybit", "").lower()
        side_kc = pos.get("side_kucoin", "").lower()
        price_spread_now = self._calculate_price_spread(current, side_bb, side_kc)

        # 2. Ambil kondisi entry & current (untuk laporan summary)
        entry_spread = pos.get("entry_spread", 0) or 0
        current_spread = current.get("spread_pct", 0) or 0
        current_delta = current.get("delta_pct", 0) or 0
        live_order = self._live_order
        entry_delta = live_order.entry_delta if live_order else current_delta

        # ── LIVE EXIT: 2 Tahap Sequential ─────────────────────────────────────
        # Tahap 1: Tunggu Diff FR <= AUTO_LIVE_CLOSE_FUNDING_DIFF, ATAU FR flip arah
        if not self._funding_threshold_met:
            # Ambil raw_fr_diff dari current scan (bisa negatif)
            current_raw = current.get("raw_fr_diff", 0) or 0
            entry_raw = live_order.entry_raw_fr_diff if live_order else current_raw

            # Cek 1: threshold biasa
            threshold_met = abs(current_delta) <= AUTO_LIVE_CLOSE_FUNDING_DIFF
            # Cek 2: FR sudah flip arah? (raw_diff berubah sign)
            flip_met = (entry_raw * current_raw < 0) if entry_raw != 0 else False

            trigger_reason = None
            if threshold_met and flip_met:
                trigger_reason = f"Diff FR turun ke `{current_delta:.4f}%` dan arah FR flip (entry: {entry_raw:+.4f}% → now: {current_raw:+.4f}%)"
            elif threshold_met:
                trigger_reason = f"Diff FR turun ke `{current_delta:.4f}%` (≤ `{AUTO_LIVE_CLOSE_FUNDING_DIFF}%`)"
            elif flip_met:
                trigger_reason = f"Arah FR flip (entry: {entry_raw:+.4f}% → now: {current_raw:+.4f}%)"

            if trigger_reason:
                self._funding_threshold_met = True
                log.info(
                    "LIVE Tahap 1 TERPENUHI: %s — %s",
                    symbol, trigger_reason
                )
                self._emit_event(
                    "state_change",
                    f"📉 *Tahap 1 Terpenuhi* | {symbol}\n\n{trigger_reason}\n\n"
                    f"🔍 _Sekarang monitoring Price Spread untuk exit..._"
                )
            else:
                log.debug("LIVE %s: menunggu Tahap 1 — Diff FR=%.4f%% (target<=%.4f%%)",
                          symbol, current_delta, AUTO_LIVE_CLOSE_FUNDING_DIFF)
            return

        # Tahap 2: Setelah Tahap 1, close jika Price Spread >= AUTO_LIVE_CLOSE_PRICE_SPREAD
        if price_spread_now >= AUTO_LIVE_CLOSE_PRICE_SPREAD:
            log.info(
                "LIVE Tahap 2 TERPENUHI → CLOSE: %s Price Spread=%.4f%% >= %.4f%%",
                symbol, price_spread_now, AUTO_LIVE_CLOSE_PRICE_SPREAD
            )
            if PAPER_MODE:
                result = self._paper.close_position(pos_id)
            else:
                log.error("Live close not yet implemented")
                return

            self._emit_event(
                "close",
                _format_trade_summary(result, symbol, entry_spread, current_spread, entry_delta, current_delta),
            )
            self._live_position_id = None
            self._live_order = None
            self._funding_threshold_met = False
            self._hedge_triggered = False
            self._entry_balance_snapshot.clear()
            self._state = State.IDLE
        else:
            log.debug("LIVE %s: Tahap 2 — menunggu spread %.4f%% >= %.4f%%",
                      symbol, price_spread_now, AUTO_LIVE_CLOSE_PRICE_SPREAD)

    # ─── REBALANCING ───────────────────────────────────────────────────

    def _start_rebalance(self, status, now: float):
        """Mulai proses rebalance dan pindah ke state REBALANCING."""
        st = status
        self._state = State.REBALANCING
        self._rebalance_engine.start_rebalance(st)

        bb_pct = round(st.ratio_bybit * 100, 1)
        kc_pct = round(st.ratio_kucoin * 100, 1)

        if PAPER_MODE:
            msg = (
                f"⚖️ *AUTO REBALANCE — PAPER MODE*\n\n"
                f"Saldo tidak seimbang. Memulai simulasi transfer...\n\n"
                f"├ Bybit:  `${st.bybit_balance:.2f}` ({bb_pct}%)\n"
                f"├ KuCoin: `${st.kucoin_balance:.2f}` ({kc_pct}%)\n"
                f"└ Total:  `${st.total:.2f}`\n\n"
                f"Transfer: `${st.amount_to_transfer:.2f}` dari *{st.from_exchange}* → *{st.to_exchange}*\n"
                f"Fee simulasi: `${st.amount_to_transfer * REBALANCE_THRESHOLD * 0.01:.2f}` (0.1%)\n"
                f"Estimasi selesai: ~5 detik\n\n"
                f"⏸ _Bot menahan trading hingga saldo seimbang..._"
            )
        else:
            msg = (
                f"⚖️ *SALDO TIDAK SEIMBANG — AKSI DIPERLUKAN*\n\n"
                f"├ Bybit:  `${st.bybit_balance:.2f}` ({bb_pct}%)\n"
                f"├ KuCoin: `${st.kucoin_balance:.2f}` ({kc_pct}%)\n"
                f"└ Total:  `${st.total:.2f}`\n\n"
                f"📋 *Transfer Manual Diperlukan:*\n"
                f"Transfer `${st.amount_to_transfer:.2f}` USDT dari *{st.from_exchange}* ke *{st.to_exchange}*\n\n"
                f"⏸ _Bot TIDAK AKAN trading hingga saldo seimbang._\n"
                f"_Cek saldo otomatis tiap {REBALANCE_CHECK_INTERVAL_SEC} detik._\n\n"
                f"_Gunakan /rebalance untuk cek status terkini._"
            )

        self._emit_event("rebalance_start", msg)
        log.info("IDLE → REBALANCING: %.2f USDT %s → %s",
                 st.amount_to_transfer, st.from_exchange, st.to_exchange)

        # Force-save state so balance change is persisted immediately
        if PAPER_MODE:
            self._paper._save_state()

    def _tick_rebalancing(self, now: float):
        """Monitor proses rebalance."""
        if not self._rebalance_engine or not self._rebalance_engine.enabled:
            self._state = State.IDLE
            return

        result = self._rebalance_engine.tick(now)

        if result == "done":
            st = self._rebalance_engine.check_balance()
            bb_pct = round(st.ratio_bybit * 100, 1)
            kc_pct = round(st.ratio_kucoin * 100, 1)

            if PAPER_MODE:
                msg = (
                    f"✅ *REBALANCE SELESAI — PAPER MODE*\n\n"
                    f"├ Bybit:  `${st.bybit_balance:.2f}` ({bb_pct}%)\n"
                    f"├ KuCoin: `${st.kucoin_balance:.2f}` ({kc_pct}%)\n"
                    f"└ Total:  `${st.total:.2f}`\n\n"
                    f"🟢 _Bot kembali aktif mencari peluang..._"
                )
            else:
                msg = (
                    f"✅ *SALDO SEIMBANG — TRADING DILANJUTKAN*\n\n"
                    f"├ Bybit:  `${st.bybit_balance:.2f}` ({bb_pct}%)\n"
                    f"├ KuCoin: `${st.kucoin_balance:.2f}` ({kc_pct}%)\n"
                    f"└ Total:  `${st.total:.2f}`\n\n"
                    f"🟢 _Bot kembali aktif mencari peluang..._"
                )

            self._emit_event("rebalance_done", msg)
            log.info("REBALANCING → IDLE: selesai. Bybit=%.2f KuCoin=%.2f",
                     st.bybit_balance, st.kucoin_balance)
            self._state = State.IDLE
        elif result == "failed":
            self._emit_event("rebalance_failed", "🔴 *REBALANCE GAGAL* — kembali IDLE")
            self._state = State.IDLE
        # result == "waiting" → stay REBALANCING, do nothing

    # ─── Helpers ────────────────────────────────────────────────────────

    def _calculate_price_spread(self, opp: dict, side_bb: str, side_kc: str) -> float:
        """Calculate price spread ((P_Long - P_Short) / P_Short * 100)."""
        bb_mark = opp.get("bybit_mark", 0) or 0
        kc_mark = opp.get("kucoin_mark", 0) or 0
        if bb_mark <= 0 or kc_mark <= 0:
            return 0.0
        p_short = bb_mark if side_bb == "sell" else kc_mark
        p_long = kc_mark if side_kc == "buy" else bb_mark
        return ((p_long - p_short) / p_short) * 100.0

    def _get_scan(self) -> List[dict]:
        """Get latest market data. Compute fresh from WS-driven caches every call,
        fall back to file-based scan."""
        if self._spread:
            # Compute fresh signal for every symbol that has price data
            symbols = self._price.all_symbols()
            if symbols:
                result = []
                for sym in symbols:
                    sig = self._spread.compute_signal(sym)
                    if not sig:
                        continue
                    # Normalize symbol to base format (e.g. "BTC/USDT:USDT" → "BTC")
                    # to match file-path schema used by scanner/paper_engine
                    sig["symbol"] = sym.split("/")[0]
                    # Convert signal dict to match old opportunity schema
                    sig["bybit_mark"] = sig.get("bybit_price", 0)
                    sig["kucoin_mark"] = sig.get("kucoin_price", 0)
                    sig["bybit_rate_pct"] = sig.get("bybit_rate_pct", 0)
                    sig["kucoin_rate_pct"] = sig.get("kucoin_rate_pct", 0)
                    sig["bybit_next_ts"] = sig.get("bybit_next_ts", 0)
                    sig["kucoin_next_ts"] = sig.get("kucoin_next_ts", 0)
                    sig["bybit_interval_h"] = sig.get("bybit_interval_h", 8)
                    sig["kucoin_interval_h"] = sig.get("kucoin_interval_h", 8)
                    sig["bybit_action"] = sig.get("bybit_action", "—")
                    sig["kucoin_action"] = sig.get("kucoin_action", "—")
                    sig["price"] = sig.get("bybit_price", 0)
                    sig["spread_pct"] = sig.get("price_spread_pct", 0)
                    sig["raw_fr_diff"] = sig.get("raw_fr_diff", 0)
                    result.append(sig)
                # Sort by funding diff descending (matching old behavior)
                result.sort(key=lambda o: o.get("funding_diff_pct", 0), reverse=True)
                self._last_scan = {"opportunities": result}
                return result

        # Fallback: file-based scan
        data = read_opportunities()
        opps = data.get("opportunities", [])
        if not opps:
            run_scan()
            data = read_opportunities()
            opps = data.get("opportunities", [])
        self._last_scan = data
        return opps

    def _in_funding_window(self, now: float) -> bool:
        """Check if any pair has funding within AUTO_ENTRY_WINDOW_MIN."""
        scan = self._get_scan()
        window_sec = AUTO_ENTRY_WINDOW_MIN * 60
        for opp in scan:
            bb_ts = opp.get("bybit_next_ts", 0) or 0
            kc_ts = opp.get("kucoin_next_ts", 0) or 0
            if bb_ts > 0 and bb_ts - now <= window_sec:
                return True
            if kc_ts > 0 and kc_ts - now <= window_sec:
                return True
        return False

    def _emit_event(self, event_type: str, message: str):
        """Emit event to callback (for Telegram notifications)."""
        event = AutoEvent(type=event_type, message=message)
        if self._event_callback:
            try:
                self._event_callback(event, self._notify_chat_id)
            except Exception:
                log.exception("Event callback failed")

    # ─── Status for /auto command ─────────────────────────────────────

    def get_status(self) -> dict:
        """Return current automation status for display."""
        state_info = {
            State.IDLE: "⏳ Waiting for funding window…",
            State.REBALANCING: "⚖️ Rebalancing — menunggu transfer selesai",
            State.LOOKING: "🔍 Scanning for best pair…",
            State.DELAY: "⏳ Delay order pending — monitoring spread…",
            State.LIVE: "📈 Position live — monitoring reversal…",
        }

        status = {
            "enabled": self._enabled,
            "state": self._state.value,
            "state_desc": state_info.get(self._state, "?"),
        }

        if self._delay_orders:
            do = self._delay_orders[0]
            status["delay"] = {
                "symbol": do.symbol,
                "side_bb": do.side_bybit,
                "side_kc": do.side_kucoin,
                "amount": do.amount_usd,
                "leverage": do.leverage,
                "spread": do.entry_price_spread,
                "delta": do.entry_delta,
                "age_seconds": round(time.time() - do.created_at, 1),
            }

        if self._live_position_id:
            status["live_position"] = self._live_position_id[:12]

        if self._state == State.REBALANCING and self._rebalance_engine:
            rb = self._rebalance_engine.get_status()
            status["rebalancing"] = {
                "bybit": rb["bybit_balance"],
                "kucoin": rb["kucoin_balance"],
                "from": rb["from_exchange"],
                "to": rb["to_exchange"],
                "amount": rb["amount_to_transfer"],
            }

        return status
