"""/mode — Show trading mode (paper/live)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from handlers.state import paper_engine


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
            "🔴 *LIVE MODE*\n\n_Trading with real exchange credentials._",
            parse_mode="Markdown",
        )
