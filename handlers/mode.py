""""/mode — Show trading mode (paper/live)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
import handlers.state as state


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PAPER_MODE and state.paper_engine:
        bal = state.paper_engine.get_balance()
        summ = state.paper_engine.get_summary()
        await update.message.reply_text(
            f"📄 PAPER MODE (Simulasi)\n\n"
            f"Saldo Bybit (sim):   ${summ.get('bybit_balance', 0):.2f}\n"
            f"Saldo KuCoin (sim):  ${summ.get('kucoin_balance', 0):.2f}\n"
            f"Total:               ${bal:.2f}\n"
            f"Posisi terbuka: {summ['open_positions']}\n"
            f"PnL direalisasi: {summ['realized_pnl']:+.2f}\n"
            f"Total PnL: {summ['total_pnl']:+.2f}\n\n"
            f"Untuk live: set PAPER_MODE=false + LIVE_CONFIRM=true di .env",
        )
    elif not PAPER_MODE and state.paper_engine:
        summ = state.paper_engine.get_summary()
        await update.message.reply_text(
            f"🔴 LIVE MODE (Dana Real)\n\n"
            f"Saldo Bybit: ${summ.get('bybit_balance', 0):.2f}\n"
            f"Saldo KuCoin: ${summ.get('kucoin_balance', 0):.2f}\n"
            f"Total: ${summ.get('balance', 0):.2f}\n"
            f"Posisi terbuka: {summ['open_positions']}\n"
            f"PnL: {summ['total_pnl']:+.2f}",
        )
    else:
        await update.message.reply_text("Engine belum siap.")