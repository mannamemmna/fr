"""/blacklist — Kelola daftar simbol yang diblokir karena delisting."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_db
from core.delisting_monitor import check_now
from core.tg_format import b, i, code, esc


async def cmd_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        entries = get_db().get_blacklist()
        if not entries:
            await update.message.reply_text("📭 Blacklist kosong — tidak ada simbol yang diblokir.")
            return
        lines = [f"{b('🚫 DELISTING BLACKLIST')}\n"]
        for e in entries[:30]:
            conf_icon = "🔴" if e["confidence"] == "high" else "🟡"
            lines.append(f"{conf_icon} {b(e['symbol'])} ({esc(e['exchange'])})\n   └ {esc(e['reason'][:80])}")
        lines.append(f"\n{i('Gunakan /blacklist remove SYMBOL untuk hapus (false positive).')}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    cmd = context.args[0].lower()

    if cmd == "remove" and len(context.args) > 1:
        symbol = context.args[1].upper()
        ok = get_db().remove_from_blacklist(symbol)
        await update.message.reply_text(
            f"✅ {esc(symbol)} dihapus dari blacklist." if ok else f"⚠️ {esc(symbol)} tidak ada di blacklist."
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
            await update.message.reply_text(f"⚠️ {esc(symbol)} tidak ada di blacklist.")
            return
        e = entries[0]
        await update.message.reply_text(
            f"{b(symbol)} ({esc(e['exchange'])}, confidence={esc(e['confidence'])})\n\n{esc(e['reason'])}\n\n{esc(e['announcement_url'])}",
            parse_mode="HTML",
        )

    else:
        await update.message.reply_text(
            f"Cara pakai:\n"
            f"<code>/blacklist</code> — lihat daftar\n"
            f"<code>/blacklist check</code> — cek pengumuman terbaru manual\n"
            f"<code>/blacklist info SYMBOL</code> — detail satu simbol\n"
            f"<code>/blacklist remove SYMBOL</code> — hapus (kalau false positive)",
            parse_mode="HTML",
        )
