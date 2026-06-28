"""FR Bot — Telegram bot entry point (thin main, handlers in handlers/)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    BOT_TOKEN, PAPER_MODE, AUTO_SCAN_INTERVAL, NOTIFY_CHAT_ID, AUTO_MODE,
    AUTO_CLOSE_ON_RESTART,
)
from core.paper_engine import PaperEngine
from core.live_engine import LiveEngine, LiveModeLockedError, MissingLiveCredentialsError
from core.automation_engine import AutomationEngine, AutoEvent
from core.rebalance_engine import RebalanceEngine
from core.bg_scanner import start_bg_scanner
from core.scheduler import register_jobs
from core.market_cache import get_price_cache, get_funding_cache
from core.ws_pool import WSPool
from core.spread_engine import get_spread_engine
from core.db import get_db
from core.scanner import run_scan

from handlers import state
from handlers.status import cmd_status
from handlers.start import cmd_start
from handlers.scan import cmd_scan
from handlers.top import cmd_top
from handlers.execute import cmd_execute
from handlers.close import cmd_close, cmd_closeall
from handlers.portfolio import cmd_portfolio
from handlers.pnl import cmd_pnl
from handlers.mode import cmd_mode
from handlers.auto import cmd_auto
from handlers.health import cmd_health
from handlers.help import cmd_help
from handlers.pair import cmd_pair
from handlers.rebalance import cmd_rebalance

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("fr-bot")


def main():
    if not BOT_TOKEN or BOT_TOKEN.startswith("your_"):
        log.error("❌ BOT_TOKEN not set in .env!")
        return 1

    log.info("Starting FR Bot…")
    log.info("Mode: %s", "PAPER" if PAPER_MODE else "LIVE")

    # ── Local DB ──
    state.db = get_db()
    state.db.log_event("INFO", "bot", "Bot starting…")

    # ── Market Cache & Spread Engine (event-driven) ──
    state.price_cache = get_price_cache()
    state.funding_cache = get_funding_cache()
    state.spread_engine = get_spread_engine()

    # ── WebSocket Connection Pool ──
    # Subscribe to common symbols on startup; updates cascade to spread engine
    state.ws_pool = WSPool(
        state.price_cache,
        state.funding_cache,
        on_spread_update=lambda ex, typ, data: state.spread_engine.on_funding_update(ex, data),
    )
    # Run one initial scan to get symbol list, then WS subscribes automatically
    log.info("Running initial scan to bootstrap WebSocket subscriptions…")
    try:
        initial = run_scan()
        state.last_scan = initial
        syms = [o["symbol"] for o in initial.get("opportunities", [])]
    except Exception:
        log.warning("Initial scan failed, WS will start with empty symbol list (subscribe after /scan)")
        syms = []
    state.ws_pool.start(syms)
    log.info("WebSocket pool started with %d symbols", len(syms))

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Engines ──
    if PAPER_MODE:
        state.paper_engine = PaperEngine()
    else:
        try:
            state.paper_engine = LiveEngine()
        except (LiveModeLockedError, MissingLiveCredentialsError) as e:
            log.error("❌ Live mode refused: %s", e)
            state.db.log_event("ERROR", "bot", f"Live mode refused: {e}")
            return 1

    # 🛡️ Restart check
    if PAPER_MODE and state.paper_engine:
        open_positions = state.paper_engine.get_open_positions()
        if open_positions:
            n = len(open_positions)
            pos_list = "\n".join(
                f"  • *{p['symbol']}* — ${p['amount_usd']:.0f} × {p.get('leverage','?')}x — `{p['id'][:8]}…`"
                for p in open_positions
            )
            if AUTO_CLOSE_ON_RESTART:
                results = state.paper_engine.close_all_positions()
                total_pnl = sum(r.get("realized_pnl", 0) for r in results)
                log.warning("Auto-closed %d orphaned positions on restart (PnL: %+.2f)", n, total_pnl)
                startup_warn = (
                    f"⚠️ *BOT RESTART — AUTO-CLOSED {n} ORPHANED POSITIONS*\n\n"
                    f"{pos_list}\n\n"
                    f"Total PnL: *{total_pnl:+.2f} USD*\n"
                    f"Balance: `${state.paper_engine.get_balance():.2f}`\n\n"
                    f"_Set AUTO_CLOSE_ON_RESTART=false in .env to disable._"
                )
            else:
                log.warning("Found %d orphaned positions on restart (NOT auto-closing)", n)
                startup_warn = (
                    f"⚠️ *BOT RESTART — {n} ORPHANED POSITIONS FOUND*\n\n"
                    f"{pos_list}\n\n"
                    f"_Close manual: /closeall_\n"
                    f"_Balance: `${state.paper_engine.get_balance():.2f}`_"
                )
            if NOTIFY_CHAT_ID:
                try:
                    import requests
                    requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={"chat_id": NOTIFY_CHAT_ID, "text": startup_warn, "parse_mode": "Markdown"},
                        timeout=5,
                    )
                except Exception as _e:
                    log.warning("Failed to send restart warning: %s", _e)

    # ── Auto Engine ──
    def _on_auto_event(event: AutoEvent, notify_chat_id: str | None = None):
        if not notify_chat_id:
            return
        import re
        import requests
        raw = event.message if event.message else ""
        plain = re.sub(r"[*`_\[\]]", "", raw)
        msg = f"🤖 Auto | {event.type}\n{plain}"
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": notify_chat_id, "text": msg},
                timeout=5,
            )
        except Exception:
            log.debug("Cannot send auto event to %s", notify_chat_id)

    if state.paper_engine:
        state.auto_engine = AutomationEngine(
            state.paper_engine,
            event_callback=_on_auto_event,
            spread_engine=state.spread_engine,
        )
        # Inject rebalance engine
        state.auto_engine._rebalance_engine = RebalanceEngine(state.paper_engine, paper_mode=PAPER_MODE)
        state.auto_engine.start()
        if NOTIFY_CHAT_ID:
            state._notify_chat_id = NOTIFY_CHAT_ID
            state.auto_engine.set_notify_chat(NOTIFY_CHAT_ID)
            log.info("Notification target: %s (from .env)", NOTIFY_CHAT_ID)
            if AUTO_MODE:
                state.auto_engine.enable()

    # ── Handlers ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("execute", cmd_execute))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("closeall", cmd_closeall))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("pair", cmd_pair))
    app.add_handler(CommandHandler("rebalance", cmd_rebalance))
 
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        import telegram.error as _te
        err = context.error
        if isinstance(err, _te.Conflict):
            log.warning("Telegram Conflict: another bot instance detected")
        else:
            log.error("Telegram error: %s", err, exc_info=err)

    app.add_error_handler(error_handler)

    if AUTO_SCAN_INTERVAL > 0:
        start_bg_scanner()

    # Built-in scheduled notifications (daily summary, health alerts, startup ping)
    register_jobs(app)

    state.db.log_event("INFO", "bot", "Bot polling started")
    log.info("Bot polling started…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    raise SystemExit(main() or 0)