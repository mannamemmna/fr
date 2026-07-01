"""/help — Show all available commands."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *FR Bot — Funding Rate Arbitrage*\n\n"
        "Bot mencari selisih Funding Rate antara Bybit dan KuCoin (Delta Neutral Arbitrage). "
        "Short pada pair dengan funding rate tinggi, Long pada yang rendah.\n\n"
        "📡 *Scan & Analisa*\n"
        "`/scan` — Scan funding rate terbaru Bybit & KuCoin. Tampilkan Top 5 by Diff FR & Top 5 by APR.\n"
        "`/top [N]` — Daftar Top N pair berdasarkan Funding Difference terbesar (default 10).\n"
        "`/pair SYM` — Detail satu pair: price spread, funding rate, countdown, APR, dll. Contoh: `/pair BTC`.\n\n"
        "💼 *Trading Manual*\n"
        "`/execute SYM [amount] [lev]` — Buka posisi manual. Contoh: `/execute TAIKO 100 3`.\n"
        "`/close ID` — Tutup satu posisi berdasarkan ID.\n"
        "`/closeall` — Tutup SEMUA posisi (tombol darurat).\n\n"
        "📊 *Info & Status*\n"
        "`/status` — Dashboard: mode, saldo, koneksi API, status Auto Engine.\n"
        "`/portfolio` — Posisi terbuka detail (entry price, likuidasi, next payment tiap exchange, arah, PnL).\n"
        "`/pnl` — Ringkasan Untung/Rugi (1D, 7D, 30D, Total).\n"
        "`/mode` — Cek mode bot: PAPER (simulasi) atau LIVE (real).\n"
        "`/health` — Test koneksi & ping latency ke Bybit dan KuCoin.\n\n"
        "🤖 *Automation (Auto-Trade)*\n"
        "`/auto on` — Aktifkan automation. Bot cari peluang & eksekusi otomatis.\n"
        "`/auto off` — Matikan automation (posisi berjalan tetap aman).\n"
        "`/auto status` — Cek status mesin automation saat ini.\n\n"
        "⚖️ *Rebalance*\n"
        "`/rebalance` — Cek saldo kedua exchange & status keseimbangan.\n"
        "`/rebalance on` — Aktifkan auto rebalance.\n"
        "`/rebalance off` — Nonaktifkan auto rebalance.\n\n"
        "🛠️ *Dasar*\n"
        "`/start` — Pesan sambutan / pengenalan.\n"
        "`/help` — Bantuan lengkap semua command (ini)."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
