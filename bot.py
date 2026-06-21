"""FR Bot — main Telegram bot entry point.

Handlers:
    /start      — show status & available commands
    /scan       — trigger a fresh scan
    /top [n]    — show top N opportunities
    /execute    — execute a trade (paper or live)
    /close      — close a position by ID
    /portfolio  — show portfolio summary
    /pnl        — show profit/loss breakdown
    /mode       — show current mode (paper/live)
    /help       — show help
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    BOT_TOKEN, PAPER_MODE, AUTO_SCAN_INTERVAL, NOTIFY_CHAT_ID, AUTO_MODE,
    DEFAULT_TOP_N, DEFAULT_LEVERAGE, DATA_DIR,
)
from core.scanner import run_scan, read_opportunities
from core.paper_engine import PaperEngine
from core.automation_engine import AutomationEngine, AutoEvent

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("fr-bot")

# ─── Global engine ────────────────────────────────────────────────────────
paper_engine = PaperEngine() if PAPER_MODE else None
last_scan: dict = {}
auto_engine: AutomationEngine | None = None

# Notifications
_notify_chat_id: str | None = None        # set by /auto on
exchange_health = {"bybit": True, "kucoin": True}  # current known state

# ─── Helper: format opportunity ───────────────────────────────────────────

def _format_opp(o: dict, rank: int = 0) -> str:
    prefix = f"#{rank} " if rank else ""
    symbol = o["symbol"]
    spread = o["spread_pct"]
    dir_short = o["direction"]
    apr = o["annual_pct"]
    delta = o.get("delta_pct", 0)
    bb_rate = o.get("bybit_rate_pct", 0)
    kc_rate = o.get("kucoin_rate_pct", 0)
    bb_time = o.get("bybit_next_time", "—")
    kc_time = o.get("kucoin_next_time", "—")

    pos = "+" if spread >= 0 else ""
    emoji = "🟢" if apr > 500 else "🟡" if apr > 200 else "⚪"

    return (
        f"{emoji} *{prefix}{symbol}*  |  APR: `{apr:+.1f}%`\n"
        f"   Spread: `{pos}{spread:.4f}%`  |  Δ NET: `{delta:.4f}%`\n"
        f"   {dir_short}\n"
        f"   BB: `{bb_rate:+.4f}%` ({bb_time})  KC: `{kc_rate:+.4f}%` ({kc_time})"
    )


# ─── Handlers ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "📄 *PAPER MODE*" if PAPER_MODE else "🔴 *LIVE MODE*"
    msg = (
        f"🤖 *FR Bot — Funding Rate Arbitrage*\n\n"
        f"{mode}\n"
        f"Exchanges: Bybit × KuCoin\n\n"
        f"*Commands:*\n"
        f"/scan — Trigger a fresh scan\n"
        f"/top \\[n\\] — Show top N opportunities\n"
        f"/execute `<symbol>` `<amount>` — Execute a trade\n"
        f"/close `<position_id>` — Close a position\n"
        f"/closeall — Close all open positions\n"
        f"/portfolio — Show open positions\n"
        f"/pnl — P&L summary\n"
        f"/mode — Show current mode\n"
        f"/auto — Auto trading on/off/status\n"
        f"/health — Check exchange connectivity\n"
        f"/help — This message"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_scan
    msg = await update.message.reply_text("🔍 Scanning funding rates…")

    try:
        payload = run_scan()
        last_scan = payload
        opps = payload["opportunities"]
        dur = payload["scan_duration"]
        bb = payload["bybit_count"]
        kc = payload["kucoin_count"]
        common = payload["common_count"]

        top5 = "\n\n".join(_format_opp(o, i + 1) for i, o in enumerate(opps[:5]))

        await msg.edit_text(
            f"✅ *Scan complete in {dur:.1f}s*\n"
            f"Bybit: {bb} pairs | KuCoin: {kc} pairs | Common: {common}\n\n"
            f"*🏆 TOP 5 BY SPREAD*\n\n{top5}",
            parse_mode="Markdown",
        )
    except Exception as e:
        await msg.edit_text(f"❌ Scan failed: {e}")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_scan
    n = DEFAULT_TOP_N
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            pass
    n = max(1, min(n, 30))

    if not last_scan:
        last_scan = read_opportunities()
    if not last_scan.get("opportunities"):
        await update.message.reply_text("⚠️ No scan data yet. Run /scan first.")
        return

    opps = last_scan["opportunities"]
    ts = last_scan.get("timestamp", "unknown")
    top = "\n\n".join(_format_opp(o, i + 1) for i, o in enumerate(opps[:n]))

    await update.message.reply_text(
        f"*🏆 TOP {n} BY SPREAD*\n_Scan: {ts}_\n\n{top}",
        parse_mode="Markdown",
    )


async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Usage: `/execute <symbol> <amount_usd> [leverage]`\n"
            "Example: `/execute BTC 100` (default 2x)\n"
            "Example: `/execute ETH 50 3` (3x leverage)\n\n"
            "`amount_usd` = margin/collateral\n"
            "`leverage` = 1–20x (position = margin × leverage)\n"
            "Bot uses the direction from the latest scan automatically.",
            parse_mode="Markdown",
        )
        return

    symbol = context.args[0].upper()
    amount = 100.0
    leverage = DEFAULT_LEVERAGE

    if len(context.args) > 1:
        try:
            amount = float(context.args[1])
        except ValueError:
            await update.message.reply_text(f"Invalid amount: {context.args[1]}")
            return
    if len(context.args) > 2:
        try:
            leverage = int(context.args[2])
            leverage = max(1, min(leverage, 20))
        except ValueError:
            await update.message.reply_text(f"Invalid leverage: {context.args[2]}")
            return

    # Find the symbol in latest scan
    global last_scan
    if not last_scan:
        last_scan = read_opportunities()

    opp = None
    for o in last_scan.get("opportunities", []):
        if o["symbol"].upper() == symbol:
            opp = o
            break

    if not opp:
        await update.message.reply_text(
            f"❌ Symbol `{symbol}` not found in latest scan. Run /scan first.",
            parse_mode="Markdown",
        )
        return

    # Map direction to sides
    bybit_action = opp["bybit_action"]
    kucoin_action = opp["kucoin_action"]
    if bybit_action == "—":
        await update.message.reply_text(f"⚠️ Spread is flat for {symbol}, no trade.")
        return

    side_bb = "sell" if bybit_action == "SHORT" else "buy"
    side_kc = "sell" if kucoin_action == "SHORT" else "buy"

    if PAPER_MODE:
        result = paper_engine.execute_instant(symbol, amount, side_bb, side_kc, leverage)
    else:
        await update.message.reply_text("🔴 Live execution not yet implemented. Stay tuned!")
        return

    if result["status"] == "done":
        pos = result.get("position", {})
        lev = pos.get("leverage", leverage)
        pos_size = pos.get("position_size", amount * leverage)
        await update.message.reply_text(
            f"✅ *Order executed!*\n\n"
            f"ID: `{result['task_id'][:12]}…`\n"
            f"Symbol: *{symbol}*\n"
            f"Margin: `${amount:.0f}` × {lev}x = `${pos_size:.0f}` position\n"
            f"Direction: {opp['direction']}\n"
            f"Spread: `{opp['spread_pct']:+.4f}%`\n"
            f"APR: `{opp['annual_pct']:+.1f}%`\n\n"
            f"Bal: `${paper_engine.get_balance():.2f}`" if PAPER_MODE else "",
            parse_mode="Markdown",
        )
    else:
        errors = "\n".join(result.get("errors", ["unknown"]))
        await update.message.reply_text(f"❌ Failed:\n{errors}")


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: `/close <position_id>`\n"
            "Find IDs with /portfolio",
            parse_mode="Markdown",
        )
        return

    pos_id = context.args[0]
    if PAPER_MODE:
        result = paper_engine.close_position(pos_id)
    else:
        await update.message.reply_text("🔴 Live close not yet implemented.")
        return

    if result.get("ok"):
        await update.message.reply_text(
            f"✅ *Position closed!*\n\n"
            f"ID: `{pos_id[:12]}…`\n"
            f"Symbol: *{result.get('symbol', '?')}*\n"
            f"Realized PnL: `{result.get('realized_pnl', 0):+.2f} USD`\n"
            f"  Price PnL: `{result.get('price_pnl', 0):+.2f}`\n"
            f"  Funding: `{result.get('funding_pnl', 0):+.2f}`\n"
            f"  Fees: `{result.get('fees', 0):.2f}`\n\n"
            f"Bal: `${paper_engine.get_balance():.2f}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ {result.get('error', 'unknown error')}")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAPER_MODE:
        await update.message.reply_text("🔴 Live closeall not yet implemented.")
        return

    positions = paper_engine.get_open_positions()
    if not positions:
        await update.message.reply_text("📭 No open positions.")
        return

    await update.message.reply_text(f"🔄 Closing {len(positions)} positions…")
    results = paper_engine.close_all_positions()

    total_pnl = sum(r.get("realized_pnl", 0) for r in results)
    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok

    await update.message.reply_text(
        f"✅ Closed {ok}/{len(results)} positions\n"
        f"❌ Failed: {fail}\n"
        f"Total PnL: `{total_pnl:+.2f} USD`\n"
        f"Bal: `${paper_engine.get_balance():.2f}`",
        parse_mode="Markdown",
    )


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PAPER_MODE:
        summary = paper_engine.get_summary()
        positions = summary["positions"]
    else:
        await update.message.reply_text("🔴 Live portfolio not yet implemented.")
        return

    if not positions:
        await update.message.reply_text(
            f"📭 *No open positions*\n\n"
            f"💰 Balance: `${summary['balance']:.2f}`\n"
            f"📊 Realized PnL: `{summary['realized_pnl']:+.2f}`\n"
            f"📈 Total PnL: `{summary['total_pnl']:+.2f}`",
            parse_mode="Markdown",
        )
        return

    lines = [
        f"*📊 PORTFOLIO*\n"
        f"💰 Balance: `${summary['balance']:.2f}`  "
        f"Exposure: `${summary['total_exposure']:.2f}`\n"
        f"📊 Realized: `{summary['realized_pnl']:+.2f}`  "
        f"Unrealized: `{summary['unrealized_pnl']:+.2f}`\n"
        f"📈 Total PnL: `{summary['total_pnl']:+.2f}`\n",
        f"*Open Positions ({len(positions)}):*",
    ]

    for p in positions:
        pid = p["id"][:10]
        sym = p["symbol"]
        margin = p["amount_usd"]
        lev = p.get("leverage", "?")
        pos_size = p.get("position_size", margin)
        spread = p.get("entry_spread", "—")
        entry_time = p.get("entry_time", "—")[:19]
        dir_str = f"{p['side_bybit'].upper()} BB / {p['side_kucoin'].upper()} KC"

        # Compute unrealized PnL for this position
        opp = None
        global last_scan
        if not last_scan:
            last_scan = read_opportunities()
        for o in last_scan.get("opportunities", []):
            if o["symbol"].upper() == sym.upper():
                opp = o
                break

        upnl = "—"
        if opp:
            exit_bb = opp.get("bybit_mark") or opp.get("price", 0)
            exit_kc = opp.get("kucoin_mark") or opp.get("price", 0)
            entry_bb = p.get("entry_price_bybit", 0)
            entry_kc = p.get("entry_price_kucoin", 0)
            qty = p.get("quantity", 0)
            if p["side_bybit"] == "buy":
                pnl_bb = qty * (exit_bb - entry_bb)
            else:
                pnl_bb = qty * (entry_bb - exit_bb)
            if p["side_kucoin"] == "buy":
                pnl_kc = qty * (exit_kc - entry_kc)
            else:
                pnl_kc = qty * (entry_kc - exit_kc)
            upnl = f"`{(pnl_bb + pnl_kc):+.2f}`"

        lines.append(
            f"`{pid}…` *{sym}* — Margin: `${margin:.0f}` × {lev}x = `${pos_size:.0f}`\n"
            f"  {dir_str}  |  Spread: `{spread}%`  |  uPnL: {upnl}\n"
            f"  _Close: /close {pid}_"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAPER_MODE:
        await update.message.reply_text("🔴 Live PnL not yet implemented.")
        return

    summary = paper_engine.get_summary()
    closed = paper_engine.get_closed_positions()

    lines = [
        f"*💰 P&L SUMMARY*\n",
        f"Balance: `${summary['balance']:.2f}`  "
        f"(Initial: `${summary['initial_balance']:.2f}`)\n",
        f"Realized PnL: `{summary['realized_pnl']:+.2f} USD`\n"
        f"Unrealized PnL: `{summary['unrealized_pnl']:+.2f} USD`\n"
        f"Total PnL: `{summary['total_pnl']:+.2f} USD`\n",
        f"Fees paid: `{summary['total_fees']:.2f} USD`\n"
        f"Est. Funding earned: `{summary['total_funding_pnl']:.2f} USD`\n",
    ]

    if closed:
        lines.append(f"*Last 5 closed trades:*")
        for p in closed[-5:]:
            sym = p["symbol"]
            pnl = p.get("realized_pnl", 0)
            spread = p.get("entry_spread", "—")
            total_fee = p.get("total_fee", 0)
            total_price_pnl = p.get("total_price_pnl", 0)
            funding = p.get("funding_pnl", 0)

            sign = "✅" if pnl >= 0 else "❌"
            lines.append(
                f"{sign} *{sym}*  PnL: `{pnl:+.2f}`  "
                f"(Price: `{total_price_pnl:+.2f}` "
                f"Funding: `{funding:+.2f}` "
                f"Fees: `-{total_fee:.2f}`)"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PAPER_MODE:
        bal = paper_engine.get_balance()
        summ = paper_engine.get_summary()
        await update.message.reply_text(
            f"📄 *PAPER MODE* _(simulated trading)_\n\n"
            f"Balance: `${bal:.2f} USDT`\n"
            f"Open positions: {summ['open_positions']}\n"
            f"Realized PnL: `{summ['realized_pnl']:+.2f}`\n"
            f"Total PnL: `{summ['total_pnl']:+.2f}`\n\n"
            f"_Set PAPER_MODE=false in .env to switch to live trading._",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"🔴 *LIVE MODE*\n\n"
            f"_Trading with real exchange credentials._",
            parse_mode="Markdown",
        )


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable or disable auto trading mode."""
    global auto_engine
    if not auto_engine:
        await update.message.reply_text("⚠️ Automation engine not initialized.")
        return

    if not context.args:
        # Show status
        s = auto_engine.get_status()
        st = "🟢 ON" if s["enabled"] else "🔴 OFF"
        extra = ""
        if s.get("delay"):
            d = s["delay"]
            extra = (
                f"\n\n⏳ *Delay Order*\n"
                f"Pair: *{d['symbol']}* | {d['side_bb'].upper()} BB / {d['side_kc'].upper()} KC\n"
                f"Margin: `${d['amount']:.0f}` × {d['leverage']}x\n"
                f"Price spread: `{d['spread']:+.4f}%` (BB–KC)  |  Delta: `{d['delta']:.4f}%`\n"
                f"Stable: `{d['stable']}` | Age: {d['age_seconds']:.0f}s"
            )
        if s.get("live_position"):
            extra += f"\n\n📈 Live Position: `{s['live_position']}…`"

        await update.message.reply_text(
            f"*🤖 AUTO ENGINE*\n\n"
            f"Status: {st}\n"
            f"State: `{s['state']}` — {s['state_desc']}{extra}\n\n"
            f"_/auto on | /auto off_",
            parse_mode="Markdown",
        )
        return

    cmd = context.args[0].lower()
    chat_id = str(update.effective_chat.id)
    if cmd == "on":
        global _notify_chat_id
        _notify_chat_id = chat_id
        auto_engine.enable()
        auto_engine.set_notify_chat(chat_id)
        await update.message.reply_text(
            f"🟢 *Auto mode ON* — engine akan scan & eksekusi otomatis\n"
            f"_Chat ini didaftarkan untuk notifikasi_\n\n"
            f"_Tip: set NOTIFY_CHAT_ID di .env biar notifikasi langsung jalan tanpa /auto on_",
            parse_mode="Markdown",
        )
    elif cmd == "off":
        auto_engine.disable()
        await update.message.reply_text("🔴 *Auto mode OFF* — semua pending order dicancel", parse_mode="Markdown")
    else:
        await update.message.reply_text("Usage: /auto [on|off]", parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check exchange connectivity."""
    global exchange_health

    lines = ["*🏥 EXCHANGE HEALTH*\n"]
    for name in ("bybit", "kucoin"):
        try:
            client = __import__("exchanges", fromlist=["get_client"]).get_client(name)
            client.fetch_all_funding_rates()
            exchange_health[name] = True
            lines.append(f"🟢 *{name.upper()}* — OK")
        except Exception as e:
            exchange_health[name] = False
            lines.append(f"🔴 *{name.upper()}* — DOWN: `{e}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _send_alert(application, message: str):
    """Thread-safe notification to registered chat."""
    global _notify_chat_id
    if not _notify_chat_id:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            application.bot.send_message(
                chat_id=_notify_chat_id, text=message, parse_mode="Markdown"
            ),
            application.loop,
        )
    except Exception:
        log.debug("Cannot send alert to %s", _notify_chat_id)


# ─── Background scanner ───────────────────────────────────────────────────

_bg_scanner_thread = None
_bg_scanner_stop = threading.Event()


def _bg_scanner_loop(application):
    """Background thread: auto-scan every AUTO_SCAN_INTERVAL seconds."""
    global last_scan, exchange_health
    log.info("Background scanner started (interval=%ds)", AUTO_SCAN_INTERVAL)
    while not _bg_scanner_stop.is_set():
        try:
            payload = run_scan()
            last_scan = payload
            n_opps = len(payload.get("opportunities", []))
            log.info("Auto-scan: %d opportunities in %.1fs", n_opps, payload.get("scan_duration", 0))

            # Exchange back online after being down
            for name in ("bybit", "kucoin"):
                count_key = f"{name}_count"
                if not exchange_health.get(name, True) and payload.get(count_key, 0) > 0:
                    exchange_health[name] = True
                    _send_alert(application, f"🟢 *{name.upper()}* is back online!")

        except Exception as e:
            err_msg = str(e)[:200]
            log.error("Auto-scan failed: %s", err_msg)

            # Detect which exchange is down
            for name in ("bybit", "kucoin"):
                if name.lower() in err_msg.lower() or "timeout" in err_msg.lower() or "connect" in err_msg.lower():
                    if exchange_health.get(name, True):
                        exchange_health[name] = False
                        _send_alert(application, f"🔴 *{name.upper()}* DOWN — `{err_msg}`")

            _send_alert(application, f"⚠️ Auto-scan failed:\\n`{err_msg}`")

        _bg_scanner_stop.wait(AUTO_SCAN_INTERVAL)


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN or BOT_TOKEN.startswith("your_"):
        log.error("❌ BOT_TOKEN not set in .env! Copy .env.example → .env and fill in your token.")
        return 1

    log.info("Starting FR Bot…")
    log.info("Mode: %s", "PAPER" if PAPER_MODE else "LIVE")

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Automation Engine ──
    global auto_engine, paper_engine

    # 🛡️ Restart check: warn about orphaned positions
    if PAPER_MODE and paper_engine:
        open_positions = paper_engine.get_open_positions()
        if open_positions:
            from config import AUTO_CLOSE_ON_RESTART
            n = len(open_positions)
            pos_list = "\n".join(
                f"  • *{p['symbol']}* — ${p['amount_usd']:.0f} × {p.get('leverage','?')}x — `{p['id'][:8]}…`"
                for p in open_positions
            )

            if AUTO_CLOSE_ON_RESTART:
                results = paper_engine.close_all_positions()
                total_pnl = sum(r.get("realized_pnl", 0) for r in results)
                sign = "✅" if total_pnl >= 0 else "❌"
                log.warning("Auto-closed %d orphaned positions on restart (PnL: %+.2f)", n, total_pnl)
                startup_warn = (
                    f"⚠️ *BOT RESTART — AUTO-CLOSED {n} ORPHANED POSITIONS*\n\n"
                    f"{pos_list}\n\n"
                    f"Total PnL: *{total_pnl:+.2f} USD*\n"
                    f"Balance: `${paper_engine.get_balance():.2f}`\n\n"
                    f"_Posisi ini floating tanpa monitor — auto-close diaktifkan._\n"
                    f"_Set AUTO_CLOSE_ON_RESTART=false di .env to disable._"
                )
            else:
                log.warning("Found %d orphaned positions on restart (NOT auto-closing)", n)
                startup_warn = (
                    f"⚠️ *BOT RESTART — {n} ORPHANED POSITIONS FOUND*\n\n"
                    f"{pos_list}\n\n"
                    f"_Posisi ini floating tanpa monitor!_ "
                    f"_Close manual: /closeall atau set AUTO_CLOSE_ON_RESTART=true_\n\n"
                    f"_Balance: `${paper_engine.get_balance():.2f}`_"
                )

            # Send warning to notify chat if configured
            if NOTIFY_CHAT_ID:
                import requests as _r
                try:
                    _r.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={"chat_id": NOTIFY_CHAT_ID, "text": startup_warn, "parse_mode": "Markdown"},
                        timeout=5,
                    )
                except Exception as _e:
                    log.warning("Failed to send restart warning: %s", _e)
    # ── End restart check ──

    # Event callback: bridge from thread to Telegram async
    def _on_auto_event(event: AutoEvent, notify_chat_id: str | None = None):
        """Called from automation thread → forward to registered chat."""
        if not notify_chat_id:
            return
        msg = f"🤖 *Auto* | `{event.type}`\n{event.message}"
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(chat_id=notify_chat_id, text=msg, parse_mode="Markdown"),
                app.loop,
            )
        except Exception:
            log.debug("Cannot send auto event to %s", notify_chat_id)

    if PAPER_MODE and paper_engine:
        auto_engine = AutomationEngine(paper_engine, event_callback=_on_auto_event)
        auto_engine.start()

        # Auto-register notification target from .env (no /auto on needed)
        global _notify_chat_id
        if NOTIFY_CHAT_ID:
            _notify_chat_id = NOTIFY_CHAT_ID
            auto_engine.set_notify_chat(NOTIFY_CHAT_ID)
            log.info("Notification target: %s (from .env)", NOTIFY_CHAT_ID)

            # Auto-enable if AUTO_MODE=true
            if AUTO_MODE:
                auto_engine.enable()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
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

    # Background scanner
    if AUTO_SCAN_INTERVAL > 0:
        global _bg_scanner_thread
        _bg_scanner_thread = threading.Thread(
            target=_bg_scanner_loop,
            args=(app,),
            daemon=True,
            name="bg-scanner",
        )
        _bg_scanner_thread.start()
        log.info("Auto-scan enabled: every %ds", AUTO_SCAN_INTERVAL)

    log.info("Bot polling started…")
    app.run_polling()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
