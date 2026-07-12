"""/start — Welcome / introduction message."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from core.tg_format import b, i, code


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "📄 Paper (Simulasi)" if PAPER_MODE else "🔴 Live (Real)"
    msg = (
        f"🤖 {b('FR Bot — Funding Rate Arbitrage')}\n\n"
        f"Selamat datang! Bot ini mencari selisih {b('Funding Rate')} antara Bybit dan KuCoin (Delta Neutral Arbitrage). "
        f"Bot akan melakukan {b('Short')} pada pair yang harganya lebih mahal/funding rate-nya tinggi, "
        f"dan {b('Long')} pada pair yang lebih murah/funding rate-nya rendah, untuk menangkap profit bebas risiko dari pembayaran funding fee.\n\n"
        f"Mode saat ini: {code(mode)}\n\n"
        f"Gunakan <code>/help</code> untuk melihat daftar lengkap semua command.\n\n"
        f"{i('Pastikan NOTIFY_CHAT_ID sudah diisi di .env agar notifikasi otomatis berjalan')}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
