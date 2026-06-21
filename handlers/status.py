"""/status — Real-time status dashboard."""

from __future__ import annotations
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE, AUTO_SCAN_INTERVAL, AUTO_MONITOR_INTERVAL, AUTO_ENTRY_WINDOW_MIN
from core.scanner import run_scan, read_opportunities
from handlers.state import paper_engine, auto_engine, last_scan, exchange_health


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_scan
    now = time.time()

    # ── Mode ──
    mode = "📄 Paper" if PAPER_MODE else "🔴 Live"

    # ── Balance ──
    if PAPER_MODE and paper_engine:
        summary = paper_engine.get_summary()
        balance = summary.get("balance", 0)
        total_pnl = summary.get("total_pnl", 0)
        realized = summary.get("realized_pnl", 0)
        open_positions = summary.get("positions", [])
    else:
        balance = "—"
        total_pnl = "—"
        realized = "—"
        open_positions = []

    # ── Exchange health ──
    bb_ok = exchange_health.get("bybit", True)
    kc_ok = exchange_health.get("kucoin", True)
    bb_icon = "🟢" if bb_ok else "🔴"
    kc_icon = "🟢" if kc_ok else "🔴"

    # ── Auto engine state ──
    if auto_engine:
        eng = auto_engine.get_status()
        eng_enabled = eng.get("enabled", False)
        eng_state = eng.get("state_desc", "—")
        if eng.get("delay"):
            d = eng["delay"]
            eng_detail = (
                f"🔸 Pair: *{d['symbol']}*  |  {d['side_bb'].upper()}/{d['side_kc'].upper()}\n"
                f"🔸 Delta: `{d['delta']:.4f}%`  |  Stable: {d['stable']}\n"
                f"🔸 Amount: ${d['amount']:.0f} × {d['leverage']}x"
            )
        elif eng.get("live_position"):
            eng_detail = f"🔸 Position: `{eng['live_position']}…`"
        else:
            eng_detail = f"🔸 State: {eng_state}"
    else:
        eng_enabled = False
        eng_state = "Not running"
        eng_detail = "Live mode not yet supported"

    # ── Next funding ──
    next_funding = "—"
    if not last_scan:
        last_scan = read_opportunities()
    opps = last_scan.get("opportunities", [])
    if opps:
        best = opps[0]
        bb_ts = best.get("bybit_next_ts", 0) or 0
        kc_ts = best.get("kucoin_next_ts", 0) or 0
        next_ts = min(t for t in (bb_ts, kc_ts) if t > 0)
        if next_ts > now:
            mins = int((next_ts - now) / 60)
            next_funding = f"~{mins} min"

    # ── Render ──
    lines = [
        f"*🤖 FR Bot Status*",
        "",
        f"🔹 Mode: `{mode}`",
        f"🔹 Balance: `${balance if isinstance(balance, (int, float)) else balance}`",
        f"🔹 Total PnL: `{total_pnl if isinstance(total_pnl, str) else f'{total_pnl:+.2f}'}`",
        f"🔹 Realized: `{realized if isinstance(realized, str) else f'{realized:+.2f}'}`",
        "",
        f"*🔗 Exchange*",
        f"🔹 {bb_icon} Bybit — {kc_icon} KuCoin",
        f"🔹 Scan every: {AUTO_SCAN_INTERVAL}s",
        "",
        f"*⚙️ Auto Engine*",
        f"🔹 Enabled: {'✅ Yes' if eng_enabled else '❌ No'}",
        eng_detail,
        f"🔹 Entry window: {AUTO_ENTRY_WINDOW_MIN} min before funding",
        f"🔹 Monitor interval: {AUTO_MONITOR_INTERVAL}s",
        "",
    ]

    if open_positions:
        lines.append(f"*📊 Open Positions ({len(open_positions)})*")
        for p in open_positions:
            pid = p["id"][:8]
            sym = p["symbol"]
            side = f"{p['side_bybit'].upper()}/{p['side_kc'].upper()}"
            margin = p.get("amount_usd", 0)
            lev = p.get("leverage", "?")
            size = margin * lev
            lines.append(f"  `{pid}` {sym} — ${margin:.0f}×{lev}x=${size:.0f} {side}")

    if next_funding != "—":
        lines.append("")
        lines.append(f"*⏰ Next Funding*")
        lines.append(f"  {next_funding} (top pair: {opps[0]['symbol']})")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
