"""/close POS_ID + /closeall — Close positions."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

import handlers.state as state


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    if not context.args:
        await update.message.reply_text(
            "Cara pakai: `/close <id_posisi>`\nLihat ID di /portfolio",
            parse_mode="Markdown",
        )
        return

    pos_id = context.args[0]
    result = state.paper_engine.close_position(pos_id)

    if result.get("ok"):
        await update.message.reply_text(
            f"✅ *Posisi ditutup!*\n\n"
            f"ID: `{pos_id[:12]}`\n"
            f"Simbol: *{result.get('symbol', '?')}*\n"
            f"PnL Direalisasi: `{result.get('realized_pnl', 0):+.2f} USD`\n"
            f"  └─ Harga: `{result.get('price_pnl', 0):+.2f}` | Funding: `{result.get('funding_pnl', 0):+.2f}` | Fee: `{result.get('fees', 0):.2f}`\n\n"
            f"Saldo: `${state.paper_engine.get_balance():.2f}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ {result.get('error', 'error tidak diketahui')}")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    positions = state.paper_engine.get_open_positions()
    if not positions:
        await update.message.reply_text("📭 Tidak ada posisi terbuka.")
        return

    await update.message.reply_text(f"🔄 Menutup {len(positions)} posisi...")
    results = state.paper_engine.close_all_positions()

    total_pnl = sum(r.get("realized_pnl", 0) for r in results)
    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok

    await update.message.reply_text(
        f"✅ Berhasil ditutup: {ok}/{len(results)}\n"
        f"❌ Gagal: {fail}\n"
        f"Total PnL: `{total_pnl:+.2f} USD`\n"
        f"Saldo: `${state.paper_engine.get_balance():.2f}`",
        parse_mode="Markdown",
    )
