"""/top — Show top N by delta (FR high − FR low)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_TOP_N
from core.scanner import read_opportunities
from handlers.scan import _format_opp
from handlers.state import last_scan


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

    # Sort by delta (absolute delta_pct) — biggest funding rate gap
    opps = sorted(
        last_scan["opportunities"],
        key=lambda o: abs(o.get("delta_pct", 0)),
        reverse=True,
    )
    ts = last_scan.get("timestamp", "unknown")
    top = "\n\n".join(_format_opp(o, i + 1) for i, o in enumerate(opps[:n]))

    await update.message.reply_text(
        f"*🏆 TOP {n} BY DELTA*\n_Scan: {ts}_\n\n{top}",
        parse_mode="Markdown",
    )
