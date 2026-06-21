"""/help — Show all available commands with descriptions."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "*AVAILABLE COMMANDS*\n\n"
        "/scan — Scan all pairs for funding opportunities\n"
        "/top [N] — Top N by delta (default 10)\n"
        "/execute SYM [amount] [leverage] — Execute paper trade\n"
        "/close POS_ID — Close specific position\n"
        "/closeall — Close all open positions\n"
        "/portfolio — Balances + open positions\n"
        "/pnl — P&L breakdown (1D, 7D, 30D)\n"
        "/health — Exchange connectivity + ping latency\n"
        "/mode — Show trading mode\n"
        "/auto on|off|status — Auto trading engine\n"
        "/start — Intro message\n"
        "/help — This message"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
