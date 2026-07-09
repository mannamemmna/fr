"""/execute — Manual trade execution."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_LEVERAGE
from core.scanner import read_opportunities
from core.delisting_monitor import is_blacklisted
from core.db import get_db
import handlers.state as state


async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Cara pakai: `/execute <symbol> <modal_usd> [leverage]`\n"
            "Contoh: `/execute BTC 100` (default 2x)\n"
            "Contoh: `/execute ETH 50 3` (3x leverage)\n\n"
            "`modal_usd` = jaminan yang dipakai\n"
            "`leverage` = 1-20x (posisi = modal × leverage)",
            parse_mode="Markdown",
        )
        return

    symbol = context.args[0].upper()
    amount = 100.0
    leverage = DEFAULT_LEVERAGE

    if is_blacklisted(symbol):
        entry = next((b for b in get_db().get_blacklist() if b["symbol"] == symbol), None)
        reason = entry["reason"] if entry else "terdeteksi delisting"
        await update.message.reply_text(
            f"🚫 *{symbol} diblokir* — kemungkinan delisting.\n\n"
            f"_{reason}_\n\n"
            f"Gunakan `/blacklist remove {symbol}` kalau ini false positive.",
            parse_mode="Markdown",
        )
        return

    if len(context.args) > 1:
        try:
            amount = float(context.args[1])
        except ValueError:
            await update.message.reply_text(f"Modal tidak valid: {context.args[1]}")
            return
    if len(context.args) > 2:
        try:
            leverage = max(1, min(int(context.args[2]), 20))
        except ValueError:
            await update.message.reply_text(f"Leverage tidak valid: {context.args[2]}")
            return

    if not state.last_scan:
        state.last_scan = read_opportunities()

    opp = next((o for o in state.last_scan.get("opportunities", []) if o["symbol"].upper() == symbol), None)
    if not opp:
        await update.message.reply_text(
            f"❌ Simbol `{symbol}` tidak ada di scan terbaru. Jalankan /scan dulu.",
            parse_mode="Markdown",
        )
        return

    bybit_action = opp["bybit_action"]
    if bybit_action == "—":
        await update.message.reply_text(f"⚠️ Selisih FR flat untuk {symbol}, tidak ada trade.")
        return

    side_bb = "sell" if bybit_action == "SHORT" else "buy"
    side_kc = "sell" if opp["kucoin_action"] == "SHORT" else "buy"

    result = state.paper_engine.execute_instant(symbol, amount, side_bb, side_kc, leverage)

    if result["status"] == "done":
        pos = result.get("position", {})
        lev = pos.get("leverage", leverage)
        pos_size = pos.get("position_size", amount * leverage)
        dir_text = "Jual Bybit/Beli KuCoin" if side_bb == "sell" else "Beli Bybit/Jual KuCoin"
        await update.message.reply_text(
            f"✅ *Posisi dibuka!*\n\n"
            f"ID: `{result['task_id'][:12]}`\n"
            f"Simbol: *{symbol}*\n"
            f"Modal: `${amount:.0f}` × {lev}x = `${pos_size:.0f}`\n"
            f"Arah: {dir_text}\n"
            f"Selisih FR: `{opp.get('spread_pct', 0):+.4f}%`\n"
            f"Estimasi APR: `{opp.get('annual_pct', 0):+.1f}%`\n\n"
            f"Saldo: `${state.paper_engine.get_balance():.2f}`",
            parse_mode="Markdown",
        )
    else:
        errors = "\n".join(result.get("errors", ["unknown"]))
        await update.message.reply_text(f"❌ Gagal:\n{errors}")
