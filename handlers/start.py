"""/start — Welcome message with command list."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "📄 Paper (Simulasi)" if PAPER_MODE else "🔴 Live (Real)"
    msg = (
        f"🤖 *FR Bot — Funding Rate Arbitrage*\n\n"
        f"Robot ini cari selisih funding rate Bybit vs KuCoin, "
        f"lalu buka posisi di dua exchange buat dapetin profit.\n\n"
        f"Mode: `{mode}`\n\n"
        f"*Perintah Dasar*\n"
        f"┃ `/status` — Cek kondisi bot (balance, engine, health)\n"
        f"┃ `/scan` — Scan funding rate terbaru\n"
        f"┃ `/top` — Pair dengan selisih terbesar\n\n"
        f"*Trading*\n"
        f"┃ `/execute SYM` — Buka posisi manual\n"
        f"┃ `/close ID` — Tutup satu posisi\n"
        f"┃ `/closeall` — Tutup semua posisi\n"
        f"┃ `/portfolio` — Lihat posisi + balance\n\n"
        f"*Informasi*\n"
        f"┃ `/pnl` — Untung/rugi (1D / 7D / 30D)\n"
        f"┃ `/health` — Cek koneksi exchange\n"
        f"┃ `/help` — Semua perintah lengkap\n\n"
        f"*Otomatis*\n"
        f"┃ `/auto on` — Nyalakan auto trading\n"
        f"┃ `/auto off` — Matikan auto trading\n"
        f"┃ `/auto status` — Status auto engine\n\n"
        f"_Pastikan NOTIFY_CHAT_ID sudah diisi di .env_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
