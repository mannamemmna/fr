"""/start — Welcome message with command list."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "📄 Paper (Simulasi)" if PAPER_MODE else "🔴 Live (Real)"
    msg = (
        f"🤖 *FR Bot — Funding Rate Arbitrage*\n\n"
        f"Selamat datang! Bot ini mencari selisih *Funding Rate* antara Bybit dan KuCoin (Delta Neutral Arbitrage). "
        f"Bot akan melakukan *Short* pada pair yang harganya lebih mahal/funding rate-nya tinggi, "
        f"dan *Long* pada pair yang lebih murah/funding rate-nya rendah, untuk menangkap profit bebas risiko dari pembayaran funding fee.\n\n"
        f"Mode saat ini: `{mode}`\n\n"
        f"📡 *Scan & Analisa*\n"
        f"`/scan` — Scan funding rate terbaru Bybit dan KuCoin saat ini juga. Menampilkan Top 5 by Funding Diff dan Top 5 by APR.\n"
        f"`/top` — (atau /top [angka]) Menampilkan daftar Top N pair berdasarkan Funding Difference terbesar (default 10 pair).\n\n"
        f"💼 *Trading Manual*\n"
        f"`/execute SYM` — Membuka posisi secara manual di pair spesifik. Contoh: /execute BTC 100 3.\n"
        f"`/close ID` — Menutup satu posisi terbuka secara manual menggunakan ID posisinya.\n"
        f"`/closeall` — Menutup semua posisi terbuka sekaligus (tombol darurat).\n\n"
        f"📊 *Info & Status*\n"
        f"`/status` — Dashboard utama. Menampilkan ringkasan mode, saldo total, kesehatan koneksi API, dan status live Automation Engine.\n"
        f"`/portfolio` — Melihat posisi terbuka mendetail (harga entry, likuidasi, next payment, arah, selisih profit) serta rincian saldo.\n"
        f"`/pnl` — Ringkasan Untung/Rugi. Menampilkan performa dalam 1D, 7D, 30D, dan Total keseluruhan.\n"
        f"`/mode` — Melihat bot sedang berjalan di mode apa (PAPER atau LIVE).\n"
        f"`/health` — Mengetes koneksi server dan ping (latency ms) ke server Bybit dan KuCoin.\n\n"
        f"🤖 *Automation (Auto-Trade)*\n"
        f"`/auto on` — Mengaktifkan automation. Bot mulai mencari peluang dan otomatis masuk saat kondisi terpenuhi.\n"
        f"`/auto off` — Mematikan automation. Tidak akan membuka order baru (tapi tidak menutup yang sudah jalan).\n"
        f"`/auto status` — Mengecek status mesin automation saat ini.\n\n"
        f"🛠️ *Dasar*\n"
        f"`/start` — Menampilkan pesan sambutan dan daftar perintah singkat.\n"
        f"`/help` — Menampilkan daftar lengkap semua command dan deskripsinya.\n\n"
        f"_Pastikan NOTIFY_CHAT_ID sudah diisi di .env agar notifikasi otomatis berjalan_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
