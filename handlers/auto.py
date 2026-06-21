"""/auto on|off|status — Control the auto trading engine."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

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
            extra = (
                f"\n\n⏳ *Menunggu entry...*\n"
                f"Pair: *{d['symbol']}* | {d['side_bb'].upper()} Bybit / {d['side_kc'].upper()} KuCoin\n"
                f"Modal: `${d['amount']:.0f}` × {d['leverage']}x\n"
                f"Delta FR: `{d['delta']:.4f}%` | Stabil: `{d['stable']}` checks"
            )
        if s.get("live_position"):
            extra += f"\n\n📈 Posisi aktif: `{s['live_position'][:8]}...`"

        await update.message.reply_text(
            f"*🤖 AUTO ENGINE*\n\n"
            f"Status: {st}\n"
            f"State: `{s['state']}` — {s['state_desc']}{extra}\n\n"
            f"_/auto on | /auto off_",
            parse_mode="Markdown",
        )
        return

    cmd = context.args[0].lower()
    chat_id = str(update.effective_chat.id)

    if cmd == "on":
        state._notify_chat_id = chat_id
        state.auto_engine.enable()
        state.auto_engine.set_notify_chat(chat_id)
        await update.message.reply_text(
            f"🟢 *Auto mode ON*\n"
            f"Bot akan scan & eksekusi otomatis setiap funding cycle.\n\n"
            f"_Tip: set NOTIFY\\_CHAT\\_ID di .env supaya notifikasi langsung jalan tanpa /auto on_",
            parse_mode="Markdown",
        )
    elif cmd == "off":
        state.auto_engine.disable()
        await update.message.reply_text("🔴 *Auto mode OFF* — tidak ada order baru.", parse_mode="Markdown")
    elif cmd == "status":
        # Redirect ke tampilan status (panggil ulang tanpa args)
        context.args = []
        await cmd_auto(update, context)
    else:
        await update.message.reply_text("Cara pakai: /auto on | /auto off | /auto status")
