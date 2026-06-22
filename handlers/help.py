"""/help — Show all available commands."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 FR Bot — Funding Rate Arbitrage\n\n"
        "Selamat datang! Bot ini mencari selisih Funding Rate antara Bybit dan KuCoin (Delta Neutral Arbitrage). "
        "Bot akan melakukan Short pada pair yang harganya lebih mahal/funding rate-nya tinggi, "
        "dan Long pada pair yang lebih murah/funding rate-nya rendah, untuk menangkap profit bebas risiko dari pembayaran funding fee.\n\n"
        "📡 Scan & Analisa\n"
        "/scan — Scan funding rate terbaru Bybit dan KuCoin saat ini juga. Menampilkan Top 5 by Funding Diff dan Top 5 by APR.\n"
        "/top — (atau /top [angka]) Menampilkan daftar Top N pair berdasarkan Funding Difference terbesar (default 10 pair).\n\n"
        "💼 Trading Manual\n"
        "/execute SYM [amount] [leverage] — Membuka posisi secara manual di pair spesifik. Contoh: /execute BTC 100 3.\n"
        "/close ID — Menutup satu posisi terbuka secara manual menggunakan ID posisinya.\n"
        "/closeall — Menutup semua posisi terbuka sekaligus (tombol darurat).\n\n"
        "📊 Info & Status\n"
        "/status — Dashboard utama. Menampilkan ringkasan mode, saldo total, kesehatan koneksi API, dan status live Automation Engine.\n"
        "/portfolio — Melihat posisi terbuka mendetail (harga entry, likuidasi, next payment, arah, selisih profit) serta rincian saldo.\n"
        "/pnl — Ringkasan Untung/Rugi. Menampilkan performa dalam 1D, 7D, 30D, dan Total keseluruhan.\n"
        "/mode — Melihat bot sedang berjalan di mode apa (PAPER atau LIVE).\n"
        "/health — Mengetes koneksi server dan ping (latency ms) ke server Bybit dan KuCoin.\n\n"
        "🤖 Automation (Auto-Trade)\n"
        "/auto on — Mengaktifkan automation. Bot mulai mencari peluang dan otomatis masuk saat kondisi terpenuhi.\n"
        "/auto off — Mematikan automation. Tidak akan membuka order baru (tapi tidak menutup yang sudah jalan).\n"
        "/auto status — Mengecek status mesin automation saat ini.\n\n"
        "🛠️ Dasar\n"
        "/start — Menampilkan pesan sambutan dan daftar perintah singkat.\n"
        "/help — Menampilkan daftar lengkap semua command dan deskripsinya."
    )
    await update.message.reply_text(msg)
