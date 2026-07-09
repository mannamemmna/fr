"""/blacklist — Kelola daftar simbol yang diblokir karena delisting."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_db
from core.delisting_monitor import check_now


async def cmd_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        entries = get_db().get_blacklist()
        if not entries:
            await update.message.reply_text("📭 Blacklist kosong — tidak ada simbol yang diblokir.")
            return
        lines = ["*🚫 DELISTING BLACKLIST*\n"]
        for e in entries[:30]:
            conf_icon = "🔴" if e["confidence"] == "high" else "🟡"
            lines.append(f"{conf_icon} *{e['symbol']}* ({e['exchange']})\n   └ {e['reason'][:80]}")
        lines.append("\n_Gunakan /blacklist remove SYMBOL untuk hapus (false positive)._")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    cmd = context.args[0].lower()

    if cmd == "remove" and len(context.args) > 1:
        symbol = context.args[1].upper()
        ok = get_db().remove_from_blacklist(symbol)
        await update.message.reply_text(
            f"✅ {symbol} dihapus dari blacklist." if ok else f"⚠️ {symbol} tidak ada di blacklist."
        )

    elif cmd == "check":
        await update.message.reply_text("🔍 Mengecek pengumuman delisting terbaru...")
        result = check_now()
        if result["new_entries"]:
            await update.message.reply_text(
                f"🚨 {len(result['new_entries'])} simbol baru diblacklist: {', '.join(result['new_entries'])}"
            )
        else:
            await update.message.reply_text("✅ Tidak ada delisting baru terdeteksi.")

    elif cmd == "info" and len(context.args) > 1:
        symbol = context.args[1].upper()
        entries = [e for e in get_db().get_blacklist() if e["symbol"] == symbol]
        if not entries:
            await update.message.reply_text(f"⚠️ {symbol} tidak ada di blacklist.")
            return
        e = entries[0]
        await update.message.reply_text(
            f"*{symbol}* ({e['exchange']}, confidence={e['confidence']})\n\n{e['reason']}\n\n{e['announcement_url']}",
            parse_mode="Markdown",
        )

    else:
        await update.message.reply_text(
            "Cara pakai:\n"
            "`/blacklist` — lihat daftar\n"
            "`/blacklist check` — cek pengumuman terbaru manual\n"
            "`/blacklist info SYMBOL` — detail satu simbol\n"
            "`/blacklist remove SYMBOL` — hapus (kalau false positive)",
            parse_mode="Markdown",
        )