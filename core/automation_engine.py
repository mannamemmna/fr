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
from typing import Any, Dict, List, Optional

from config import (
    AUTO_MODE,
    AUTO_LEVERAGE,
    AUTO_BALANCE_PER_LEG,
    AUTO_MAX_POSITIONS,
    AUTO_MONITOR_INTERVAL,
    AUTO_ENTRY_WINDOW_MIN,
    AUTO_DELTA_THRESHOLD,
    AUTO_REVERSAL_THRESHOLD,
    AUTO_DELAY_CHECKS,
    AUTO_PREFER_SAME_INTERVAL,
    AUTO_PRICE_SPREAD_MAX_DRIFT,
    PAPER_MODE,
)
from core.scanner import run_scan, read_opportunities
from core.paper_engine import PaperEngine

log = logging.getLogger("fr-bot.auto")

# ─── State enum ────────────────────────────────────────────────────────────


class State(Enum):
    IDLE = "idle"
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
    entry_delta: float = 0.0          # Funding rate delta at entry %
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
        f"━━━ *SPREAD* ━━━",
        f"Entry: `{entry_spread:+.4f}%` → Exit: `{current_spread:+.4f}%`",
        f"Delta: `{entry_delta:.4f}%` → `{current_delta:.4f}%`",
        f"",
        f"━━━ *PRICE* ━━━",
        f"Bybit: `{entry_price_bb}` → `{exit_price_bb}`",
        f"KuCoin: `{entry_price_kc}` → `{exit_price_kc}`",
        f"",
        f"━━━ *P&L BREAKDOWN* ━━━",
        f"Price PnL: `{price_pnl:+.2f} USD`",
        f"Funding: `{funding_pnl:+.4f} USD`",
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

    def __init__(self, paper_engine: PaperEngine, event_callback=None):
        self._paper = paper_engine
        self._event_callback = event_callback
        self._state = State.IDLE
        self._enabled = AUTO_MODE
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._notify_chat_id: Optional[str] = None

        # Current state data
        self._delay_order: Optional[DelayOrder] = None
        self._live_position_id: Optional[str] = None
        self._last_scan: dict = {}
        self._last_log = time.time()

    # ─── Properties ────────────────────────────────────────────────────

    @property
    def state(self) -> State:
        return self._state

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def delay_order(self) -> Optional[DelayOrder]:
        return self._delay_order

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

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Automation engine stopped")

    def enable(self):
        with self._lock:
            self._enabled = True
        self._emit_event("state_change", "🟢 Auto mode ON")
        log.info("Auto mode ENABLED")

    def disable(self):
        with self._lock:
            self._enabled = False
            self._state = State.IDLE
            self._delay_order = None
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
        elif state == State.LOOKING:
            self._tick_looking(now)
        elif state == State.DELAY:
            self._tick_delay(now)
        elif state == State.LIVE:
            self._tick_live(now)

    # ─── IDLE ──────────────────────────────────────────────────────────

    def _tick_idle(self, now: float):
        """Wait for upcoming funding window within AUTO_ENTRY_WINDOW_MIN minutes."""
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

        # Sort by time-to-funding (closest first)
        candidates.sort(key=lambda o: min(o.get("bybit_next_ts", 0) or 0, o.get("kucoin_next_ts", 0) or 0))
        next_opp = candidates[0]
        min_ts = min(next_opp.get("bybit_next_ts", 0) or 0, next_opp.get("kucoin_next_ts", 0) or 0)
        time_left = max(0, min_ts - now)
        mins_left = time_left / 60

        log.info(
            "IDLE → LOOKING: next funding in %.0fmin (%s, delta=%.4f%%)",
            mins_left,
            next_opp["symbol"],
            next_opp["delta_pct"],
        )
        self._emit_event(
            "state_change",
            f"🔍 *Window open!* Next funding in {mins_left:.0f}min — scanning {len(candidates)} pairs…",
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

        # Filter: pairs within funding window
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
        best = candidates[0]

        # Check Bybit 1H × KuCoin 4H rule
        bb_iv = best.get("bybit_interval_h", 0) or 0
        kc_iv = best.get("kucoin_interval_h", 0) or 0
        bb_rate = best.get("bybit_rate_pct", 0) or 0
        kc_rate = best.get("kucoin_rate_pct", 0) or 0

        if bb_iv == 1 and kc_iv >= 4:
            # Only enter if Bybit FR > KuCoin
            if bb_rate <= kc_rate:
                log.info(
                    "SKIP %s: Bybit 1H (%.4f%%) ≤ KuCoin %dH (%.4f%%) — waiting for KuCoin window",
                    best["symbol"], bb_rate, kc_iv, kc_rate,
                )
                # Try next candidate
                for alt in candidates[1:]:
                    alt_bb_iv = alt.get("bybit_interval_h", 0) or 0
                    alt_kc_iv = alt.get("kucoin_interval_h", 0) or 0
                    if not (alt_bb_iv == 1 and alt_kc_iv >= 4):
                        best = alt
                        break
                else:
                    # All candidates are 1H/4H with Bybit not higher — wait
                    return

        # Validate direction
        bybit_action = best.get("bybit_action", "—")
        if bybit_action == "—":
            log.info("SKIP %s: flat spread", best["symbol"])
            return

        side_bb = "sell" if bybit_action == "SHORT" else "buy"
        side_kc = "sell" if best.get("kucoin_action") == "SHORT" else "buy"

        # Calculate price spread (mark price diff %)
        bb_mark = best.get("bybit_mark", 0) or 0
        kc_mark = best.get("kucoin_mark", 0) or 0
        price_spread = ((bb_mark - kc_mark) / kc_mark * 100) if kc_mark > 0 else 0.0

        # Create delay order
        self._delay_order = DelayOrder(
            symbol=best["symbol"],
            side_bybit=side_bb,
            side_kucoin=side_kc,
            amount_usd=AUTO_BALANCE_PER_LEG,
            leverage=AUTO_LEVERAGE,
            entry_price_spread=price_spread,
            entry_delta=best["delta_pct"],
            bybit_rate=best["bybit_rate_pct"],
            kucoin_rate=best["kucoin_rate_pct"],
            bybit_next_ts=best.get("bybit_next_ts") or 0,
            kucoin_next_ts=best.get("kucoin_next_ts") or 0,
            bybit_interval_h=bb_iv,
            kucoin_interval_h=kc_iv,
        )

        self._state = State.DELAY
        log.info(
            "LOOKING → DELAY: %s  spread=%.4f%%  delta=%.4f%%  %s  BB_iv=%dh  KC_iv=%dh",
            best["symbol"], best["spread_pct"], best["delta_pct"],
            best["direction"], bb_iv, kc_iv,
        )
        self._emit_event(
            "state_change",
            (
                f"⏳ *DELAY ORDER*\\n"
                f"Pair: *{best['symbol']}*\\n"
                f"Funding: `{best['spread_pct']:+.4f}%`  |  Delta: `{best['delta_pct']:.4f}%`\\n"
                f"Price spread: `{price_spread:+.4f}%` (BB–KC mark)\\n"
                f"Direction: {best['direction']}\\n"
                f"Interval: BB {bb_iv}h / KC {kc_iv}h\\n"
                f"Size: `${AUTO_BALANCE_PER_LEG:.0f}` × {AUTO_LEVERAGE}x = `${AUTO_BALANCE_PER_LEG * AUTO_LEVERAGE:.0f}` per leg\\n"
                f"Monitoring price spread every {AUTO_MONITOR_INTERVAL}s…"
            ),
        )

    # ─── DELAY ─────────────────────────────────────────────────────────

    def _tick_delay(self, now: float):
        """Monitor spread during delay phase. Execute if stable, cancel if reversed."""
        order = self._delay_order
        if not order:
            self._state = State.IDLE
            return

        # Check if still in funding window
        bb_ts = order.bybit_next_ts
        kc_ts = order.kucoin_next_ts
        min_ts = min(bb_ts, kc_ts)
        time_left = max(0, min_ts - now)

        if time_left <= 0:
            log.info("DELAY → IDLE: funding window expired for %s", order.symbol)
            self._emit_event("cancel", f"⏰ Delay order expired — window closed for {order.symbol}")
            self._delay_order = None
            self._state = State.IDLE
            return

        # Get latest scan for this symbol
        scan = self._get_scan()
        current = None
        for opp in scan:
            if opp["symbol"] == order.symbol:
                current = opp
                break

        if not current:
            # Symbol disappeared from scan? Re-scan.
            run_scan()
            scan = read_opportunities()
            for opp in scan.get("opportunities", []):
                if opp["symbol"] == order.symbol:
                    current = opp
                    break
            if not current:
                self._delay_order = None
                self._state = State.LOOKING
                log.warning("DELAY → LOOKING: %s disappeared from scan", order.symbol)
                return

        # Calculate current PRICE spread (BB mark vs KC mark %)
        bb_mark = current.get("bybit_mark", 0) or 0
        kc_mark = current.get("kucoin_mark", 0) or 0
        price_spread_now = ((bb_mark - kc_mark) / kc_mark * 100) if kc_mark > 0 else 0.0

        # Also check funding reversal (for early cancel)
        curr_delta = current["delta_pct"]
        entry_delta = order.entry_delta
        fund_spread_now = current.get("spread_pct", 0)

        # Reversal: funding delta dropped significantly (opportunity evaporating)
        delta_dropped = curr_delta < entry_delta * 0.3  # Lost 70%+ of delta

        # Reversal: price spread flipped sign (hedge no longer favorable)
        entry_ps = order.entry_price_spread
        price_flipped = (entry_ps > 0.1 and price_spread_now < -0.1) or (entry_ps < -0.1 and price_spread_now > 0.1)

        if delta_dropped or price_flipped:
            reason = "funding delta dropped" if delta_dropped else "price spread flipped"
            log.info(
                "DELAY reversal: %s  %s  price_spread %+.4f→%+.4f  delta %.4f→%.4f",
                order.symbol, reason, entry_ps, price_spread_now, entry_delta, curr_delta,
            )
            self._emit_event(
                "cancel",
                f"🔄 *Reversal!* {order.symbol} — cancelling delay\\n"
                f"*{reason}*\\n"
                f"Price spread: `{entry_ps:+.4f}%` → `{price_spread_now:+.4f}%`\\n"
                f"Funding delta: `{entry_delta:.4f}%` → `{curr_delta:.4f}%`\\n"
                f"_Scanning for another pair…_",
            )
            self._delay_order = None
            self._state = State.LOOKING
            return

        # Check stability: price spread hasn't moved beyond threshold
        ps_change = abs(price_spread_now - entry_ps)
        if ps_change < AUTO_PRICE_SPREAD_MAX_DRIFT:
            order.stable_checks += 1
        else:
            order.stable_checks = max(0, order.stable_checks - 1)

        if order.stable_checks >= AUTO_DELAY_CHECKS:
            # Execute!
            log.info(
                "DELAY → EXECUTE: %s stable after %d checks (price_spread=%.4f%%, drift=%.4f)",
                order.symbol, order.stable_checks, price_spread_now, ps_change,
            )
            self._execute_delay_order(order, current, time_left)
        else:
            log.debug(
                "DELAY %s: stable=%d/%d  price_spread=%.4f%% (drift %.4f)  fund=%.4f%%  left=%.0fs",
                order.symbol, order.stable_checks, AUTO_DELAY_CHECKS,
                price_spread_now, ps_change, fund_spread_now, time_left,
            )

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
            self._delay_order = None
            self._state = State.IDLE
            return

        if result["status"] == "done":
            pos = result.get("position", {})
            order.position_id = pos.get("id")
            self._live_position_id = order.position_id

            mins_left = time_left / 60
            self._state = State.LIVE
            log.info("DELAY → LIVE: %s executed, monitoring reversal", order.symbol)
            self._emit_event(
                "entry",
                (
                    f"✅ *AUTO ENTRY*\\n"
                    f"Pair: *{order.symbol}*\\n"
                    f"Margin: `${order.amount_usd:.0f}` × {order.leverage}x = `${pos.get('position_size', order.amount_usd * order.leverage):.0f}`\\n"
                    f"Price spread: `{order.entry_price_spread:+.4f}%` (BB–KC mark)\\n"
                    f"Funding: `{current['spread_pct']:+.4f}%`  |  Delta: `{current['delta_pct']:.4f}%`\\n"
                    f"Direction: {current['direction']}\\n"
                    f"⏰ Funding in: {mins_left:.0f}min\\n"
                    f"_Monitoring reversal every {AUTO_MONITOR_INTERVAL}s…_"
                ),
            )
        else:
            errors = "\n".join(result.get("errors", ["unknown"]))
            self._emit_event("error", f"❌ Auto execution failed: {errors}")
            self._delay_order = None
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
            self._delay_order = None
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

        # Check funding reversal
        entry_spread = pos.get("entry_spread", 0) or 0
        current_spread = current.get("spread_pct", 0) or 0
        spread_flipped = (entry_spread > 0 and current_spread < -AUTO_REVERSAL_THRESHOLD) or (
            entry_spread < 0 and current_spread > AUTO_REVERSAL_THRESHOLD
        )

        # Also check delta drop
        current_delta = current.get("delta_pct", 0) or 0
        delay_order = self._delay_order
        entry_delta = delay_order.entry_delta if delay_order else current_delta
        delta_collapsed = current_delta < 0.01

        if spread_flipped or delta_collapsed:
            # Auto close!
            log.info(
                "LIVE → CLOSE: reversal detected for %s  spread %+.4f→%+.4f  delta %.4f→%.4f",
                symbol, entry_spread, current_spread,
                entry_delta, current_delta,
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
            self._delay_order = None
            self._state = State.IDLE
        else:
            log.debug(
                "LIVE %s: spread=%.4f%%  delta=%.4f%%  OK",
                symbol, current_spread, current_delta,
            )

    # ─── Helpers ────────────────────────────────────────────────────────

    def _get_scan(self) -> List[dict]:
        """Get latest scan data, re-scan if stale."""
        data = read_opportunities()
        opps = data.get("opportunities", [])
        if not opps or not self._last_scan:
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
            State.LOOKING: "🔍 Scanning for best pair…",
            State.DELAY: "⏳ Delay order pending — monitoring spread…",
            State.LIVE: "📈 Position live — monitoring reversal…",
        }

        status = {
            "enabled": self._enabled,
            "state": self._state.value,
            "state_desc": state_info.get(self._state, "?"),
        }

        if self._delay_order:
            do = self._delay_order
            status["delay"] = {
                "symbol": do.symbol,
                "side_bb": do.side_bybit,
                "side_kc": do.side_kucoin,
                "amount": do.amount_usd,
                "leverage": do.leverage,
                "spread": do.entry_price_spread,
                "delta": do.entry_delta,
                "stable": f"{do.stable_checks}/{AUTO_DELAY_CHECKS}",
                "age_seconds": round(time.time() - do.created_at, 1),
            }

        if self._live_position_id:
            status["live_position"] = self._live_position_id[:12]

        return status
