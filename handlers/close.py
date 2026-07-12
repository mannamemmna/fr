"""/close POS_ID + /closeall — Close positions."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.tg_format import b, code, esc
import handlers.state as state


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    if not context.args:
        await update.message.reply_text(
            "Cara pakai: <code>/close &lt;id_posisi&gt;</code>\nLihat ID di /portfolio",
            parse_mode="HTML",
        )
        return

    pos_id = context.args[0]
    result = state.paper_engine.close_position(pos_id)

    if result.get("ok"):
        rpnl_s = f"{result.get('realized_pnl', 0):+.2f} USD"
        ppnl_s = f"{result.get('price_pnl', 0):+.2f}"
        frr_s = f"{result.get('fr_received', 0):+.2f}"
        frp_s = f"{result.get('fr_paid', 0):+.2f}"
        fee_s = f"{result.get('fees', 0):.2f}"
        bal_s = f"${state.paper_engine.get_balance():.2f}"
        await update.message.reply_text(
            f"✅ {b('Posisi ditutup!')}\n\n"
            f"ID: {code(pos_id[:12])}\n"
            f"Simbol: {b(result.get('symbol', '?'))}\n"
            f"PnL Direalisasi: {code(rpnl_s)}\n"
            f"  └─ Harga: {code(ppnl_s)} | FR diterima: {code(frr_s)} | FR dibayar: {code(frp_s)} | Fee: {code(fee_s)}\n\n"
            f"Saldo: {code(bal_s)}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(f"❌ {esc(result.get('error', 'error tidak diketahui'))}")


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
    pnl_s = f"{total_pnl:+.2f} USD"
    bal_s = f"${state.paper_engine.get_balance():.2f}"

    await update.message.reply_text(
        f"✅ Berhasil ditutup: {ok}/{len(results)}\n"
        f"❌ Gagal: {fail}\n"
        f"Total PnL: {code(pnl_s)}\n"
        f"Saldo: {code(bal_s)}",
        parse_mode="HTML",
    )
