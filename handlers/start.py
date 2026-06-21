"""/start — Intro message with command list."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "📄 PAPER MODE" if PAPER_MODE else "🔴 LIVE MODE"
    msg = (
        f"*Funding Rate Arbitrage Bot*\n\n"
        f"`{mode}`\n"
        f"Set NOTIFY_CHAT_ID in .env for auto notifications\n\n"
        f"| Command | Description |\n"
        f"|---|---|\n"
        f"| /scan | Scan all pairs |\n"
        f"| /top | Top by delta |\n"
        f"| /execute SYM | Manual entry |\n"
        f"| /portfolio | Positions + balances |\n"
        f"| /closeall | Close all |\n"
        f"| /pnl | P&L summary (1D/7D/30D) |\n"
        f"| /health | Exchange status + ping |\n"
        f"| /auto on/off/status | Auto trading |\n"
        f"| /help | All commands |"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
