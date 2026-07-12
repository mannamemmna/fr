"""/top — Show top N by funding difference."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_TOP_N
from core.scanner import read_opportunities
from core.tg_format import b, i, code, esc
from handlers.scan import _format_opp
import handlers.state as state


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = DEFAULT_TOP_N
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            pass
    n = max(1, min(n, 30))

    if not state.last_scan:
        state.last_scan = read_opportunities()
    if not state.last_scan.get("opportunities"):
        await update.message.reply_text("⚠️ No scan data yet. Run /scan first.")
        return

    # Sort by funding_diff_pct
    opps = sorted(
        state.last_scan["opportunities"],
        key=lambda o: o.get("funding_diff_pct", 0),
        reverse=True,
    )
    ts = esc(state.last_scan.get("timestamp", "unknown"))
    top = "\n\n".join(_format_opp(o, i_idx + 1) for i_idx, o in enumerate(opps[:n]))

    await update.message.reply_text(
        f"{b(f'🏆 TOP {n} BY FUNDING DIFF')}\n{i(f'Scan: {ts}')}\n\n{top}\n\n"
        f"{i('Istilah belum familiar? Ketik /help glossary untuk penjelasan.')}",
        parse_mode="HTML",
    )
