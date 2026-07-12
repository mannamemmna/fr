"""/mode — Show trading mode (paper/live)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from core.tg_format import b, code, i
import handlers.state as state


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PAPER_MODE and state.paper_engine:
        bal = state.paper_engine.get_balance()
        summ = state.paper_engine.get_summary()
        bal_s = f"${bal:.2f}"
        rpnl_s = f"{summ['realized_pnl']:+.2f}"
        tpnl_s = f"{summ['total_pnl']:+.2f}"
        await update.message.reply_text(
            f"📄 {b('PAPER MODE')} (Simulasi)\n\n"
            f"Saldo:               {code(bal_s)}\n"
            f"Posisi terbuka: {code(summ['open_positions'])}\n"
            f"PnL direalisasi: {code(rpnl_s)}\n"
            f"Total PnL: {code(tpnl_s)}\n\n"
            f"{i('Untuk live: set PAPER_MODE=false + LIVE_CONFIRM=true di .env')}",
            parse_mode="HTML",
        )
    elif not PAPER_MODE and state.paper_engine:
        summ = state.paper_engine.get_summary()
        bal_s = f"${summ.get('balance', 0):.2f}"
        tpnl_s = f"{summ['total_pnl']:+.2f}"
        await update.message.reply_text(
            f"🔴 {b('LIVE MODE')} (Dana Real)\n\n"
            f"Saldo: {code(bal_s)}\n"
            f"Posisi terbuka: {code(summ['open_positions'])}\n"
            f"PnL: {code(tpnl_s)}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("Engine belum siap.")
