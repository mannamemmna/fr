"""/auto on|off|status — Control the auto trading engine."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.tg_format import b, i, code, esc
import handlers.state as state


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.auto_engine:
        await update.message.reply_text("⚠️ Auto engine belum siap. Pastikan bot sudah berjalan dengan benar.")
        return

    if not context.args:
        s = state.auto_engine.get_status()
        st = "🟢 ON" if s["enabled"] else "🔴 OFF"
        extra = ""
        if s.get("delay"):
            d = s["delay"]
            amount_s = f"${d['amount']:.0f}"
            delta_s = f"{d.get('delta', 0):.4f}%"
            extra = (
                f"\n\n⏳ {b('Menunggu entry...')}\n"
                f"Pair: {b(d['symbol'])} | {esc(d['side_bb'].upper())} Bybit / {esc(d['side_kc'].upper())} KuCoin\n"
                f"Modal: {code(amount_s)} × {d['leverage']}x\n"
                f"Diff FR: {code(delta_s)}"
            )
        if s.get("live_positions"):
            ids = ", ".join(code(pid + "...") for pid in s["live_positions"])
            extra += f"\n\n📈 Posisi aktif ({len(s['live_positions'])}): {ids}"

        await update.message.reply_text(
            f"{b('🤖 AUTO ENGINE')}\n\n"
            f"Status: {st}\n"
            f"State: {code(s['state'])} — {esc(s['state_desc'])}{extra}\n\n"
            f"{i('/auto on | /auto off')}",
            parse_mode="HTML",
        )
        return

    cmd = context.args[0].lower()
    chat_id = str(update.effective_chat.id)

    if cmd == "on":
        state._notify_chat_id = chat_id
        state.auto_engine.enable()
        state.auto_engine.set_notify_chat(chat_id)
        await update.message.reply_text(
            f"🟢 {b('Auto mode ON')}\n"
            f"Bot akan scan &amp; eksekusi otomatis setiap funding cycle.\n\n"
            f"{i('Tip: set NOTIFY_CHAT_ID di .env supaya notifikasi langsung jalan tanpa /auto on')}",
            parse_mode="HTML",
        )
    elif cmd == "off":
        state.auto_engine.disable()
        await update.message.reply_text(f"🔴 {b('Auto mode OFF')} — tidak ada order baru.", parse_mode="HTML")
    elif cmd == "status":
        # Redirect ke tampilan status (panggil ulang tanpa args)
        context.args = []
        await cmd_auto(update, context)
    else:
        await update.message.reply_text("Cara pakai: /auto on | /auto off | /auto status")
