"""/execute — Manual trade execution."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_LEVERAGE
from core.scanner import read_opportunities
from core.delisting_monitor import is_blacklisted
from core.db import get_db
from core.tg_format import b, i, code, esc
import handlers.state as state


async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Cara pakai: <code>/execute &lt;symbol&gt; &lt;modal_usd&gt; [leverage]</code>\n"
            "Contoh: <code>/execute BTC 100</code> (default 2x)\n"
            "Contoh: <code>/execute ETH 50 3</code> (3x leverage)\n\n"
            "<code>modal_usd</code> = jaminan yang dipakai\n"
            "<code>leverage</code> = 1-20x (posisi = modal × leverage)",
            parse_mode="HTML",
        )
        return

    symbol = context.args[0].upper()
    amount = 100.0
    leverage = DEFAULT_LEVERAGE

    if is_blacklisted(symbol):
        entry = next((b_entry for b_entry in get_db().get_blacklist() if b_entry["symbol"] == symbol), None)
        reason = entry["reason"] if entry else "terdeteksi delisting"
        await update.message.reply_text(
            f"🚫 {b(symbol + ' diblokir')} — kemungkinan delisting.\n\n"
            f"{i(esc(reason))}\n\n"
            f"Gunakan <code>/blacklist remove {esc(symbol)}</code> kalau ini false positive.",
            parse_mode="HTML",
        )
        return

    if len(context.args) > 1:
        try:
            amount = float(context.args[1])
        except ValueError:
            await update.message.reply_text(f"Modal tidak valid: {esc(context.args[1])}")
            return
    if len(context.args) > 2:
        try:
            leverage = max(1, min(int(context.args[2]), 20))
        except ValueError:
            await update.message.reply_text(f"Leverage tidak valid: {esc(context.args[2])}")
            return

    if not state.last_scan:
        state.last_scan = read_opportunities()

    opp = next((o for o in state.last_scan.get("opportunities", []) if o["symbol"].upper() == symbol), None)
    if not opp:
        await update.message.reply_text(
            f"❌ Simbol {code(symbol)} tidak ada di scan terbaru. Jalankan /scan dulu.",
            parse_mode="HTML",
        )
        return

    bybit_action = opp["bybit_action"]
    if bybit_action == "—":
        await update.message.reply_text(f"⚠️ Selisih FR flat untuk {esc(symbol)}, tidak ada trade.")
        return

    side_bb = "sell" if bybit_action == "SHORT" else "buy"
    side_kc = "sell" if opp["kucoin_action"] == "SHORT" else "buy"

    result = state.paper_engine.execute_instant(symbol, amount, side_bb, side_kc, leverage)

    if result["status"] == "done":
        pos = result.get("position", {})
        lev = pos.get("leverage", leverage)
        pos_size = pos.get("position_size", amount * leverage)
        dir_text = "Jual Bybit/Beli KuCoin" if side_bb == "sell" else "Beli Bybit/Jual KuCoin"
        amount_s = f"${amount:.0f}"
        size_s = f"${pos_size:.0f}"
        spread_s = f"{opp.get('spread_pct', 0):+.4f}%"
        apr_s = f"{opp.get('annual_pct', 0):+.1f}%"
        bal_s = f"${state.paper_engine.get_balance():.2f}"
        await update.message.reply_text(
            f"✅ {b('Posisi dibuka!')}\n\n"
            f"ID: {code(result['task_id'][:12])}\n"
            f"Simbol: {b(symbol)}\n"
            f"Modal: {code(amount_s)} × {lev}x = {code(size_s)}\n"
            f"Arah: {esc(dir_text)}\n"
            f"Selisih FR: {code(spread_s)}\n"
            f"Estimasi APR: {code(apr_s)}\n\n"
            f"Saldo: {code(bal_s)}",
            parse_mode="HTML",
        )
    else:
        errors = "\n".join(esc(e) for e in result.get("errors", ["unknown"]))
        await update.message.reply_text(f"❌ Gagal:\n{errors}")
