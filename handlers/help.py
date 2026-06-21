"""/help — Show all available commands."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📋 SEMUA PERINTAH\n\n"
        "📡 Scan\n"
        "/scan — Scan funding rate terbaru\n"
        "/top 10 — Top 10 peluang terbesar\n\n"
        "💼 Trading\n"
        "/execute BTC 100 — Buka posisi BTC modal $100\n"
        "/close ID — Tutup satu posisi\n"
        "/closeall — Tutup semua posisi\n\n"
        "📊 Info\n"
        "/portfolio — Saldo + posisi terbuka\n"
        "/pnl — Untung/rugi 1D, 7D, 30D\n"
        "/status — Ringkasan kondisi bot\n"
        "/health — Koneksi exchange\n"
        "/mode — Mode trading aktif\n\n"
        "🤖 Otomatis\n"
        "/auto on — Nyalakan auto trading\n"
        "/auto off — Matikan auto trading\n"
        "/auto status — Cek status engine\n\n"
        "/start — Pesan sambutan\n"
        "/help — Tampilkan pesan ini"
    )
    await update.message.reply_text(msg)
