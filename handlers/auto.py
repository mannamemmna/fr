"""/auto on|off|status — Control the auto trading engine."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from handlers.state import auto_engine, _notify_chat_id


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_engine
    if not auto_engine:
        await update.message.reply_text("⚠️ Automation engine not initialized.")
        return

    if not context.args:
        s = auto_engine.get_status()
        st = "🟢 ON" if s["enabled"] else "🔴 OFF"
        extra = ""
        if s.get("delay"):
            d = s["delay"]
            extra = (
                f"\n\n⏳ *Delay Order*\n"
                f"Pair: *{d['symbol']}* | {d['side_bb'].upper()} BB / {d['side_kc'].upper()} KC\n"
                f"Margin: `${d['amount']:.0f}` × {d['leverage']}x\n"
                f"Price spread: `{d['spread']:+.4f}%` (BB–KC)  |  Delta: `{d['delta']:.4f}%`\n"
                f"Stable: `{d['stable']}` | Age: {d['age_seconds']:.0f}s"
            )
        if s.get("live_position"):
            extra += f"\n\n📈 Live Position: `{s['live_position']}…`"

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
        global _notify_chat_id
        _notify_chat_id = chat_id
        auto_engine.enable()
        auto_engine.set_notify_chat(chat_id)
        await update.message.reply_text(
            f"🟢 *Auto mode ON* — engine akan scan & eksekusi otomatis\n"
            f"_Chat ini didaftarkan untuk notifikasi_\n\n"
            f"_Tip: set NOTIFY_CHAT_ID di .env biar notifikasi langsung jalan tanpa /auto on_",
            parse_mode="Markdown",
        )
    elif cmd == "off":
        auto_engine.disable()
        await update.message.reply_text("🔴 *Auto mode OFF* — semua pending order dicancel", parse_mode="Markdown")
    else:
        await update.message.reply_text("Usage: /auto [on|off]", parse_mode="Markdown")
