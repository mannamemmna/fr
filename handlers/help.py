"""/help — Show all available commands + glossary for beginners."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.tg_format import b, i

GLOSSARY = {
    "Delta-neutral": "Buka posisi Long di satu exchange dan Short di exchange lain dengan ukuran sama besar. Untung/rugi karena harga naik-turun saling menutupi — profit murni datang dari selisih funding rate, bukan tebak arah harga.",
    "Funding Rate": "Biaya periodik yang dibayar antara trader Long dan Short di kontrak perpetual futures. Kalau rate positif, Long bayar ke Short (dan sebaliknya kalau negatif).",
    "Diff FR": "Selisih funding rate antara Bybit dan KuCoin untuk pair yang sama — makin besar selisihnya, makin besar potensi profit dari strategi ini.",
    "Price Spread": "Selisih harga aset yang sama antara Bybit dan KuCoin saat ini, dalam persen. Idealnya kecil/negatif saat entry, karena itu bagian dari cost strategi ini.",
    "APR": "Annual Percentage Rate — estimasi keuntungan setahun kalau kondisi funding rate saat ini bertahan terus-menerus. Angka proyeksi, bukan jaminan.",
    "Leverage": "Pengali ukuran posisi terhadap modal (margin) yang dipakai. Leverage 3x dengan modal $100 = posisi senilai $300.",
    "Margin": "Modal/jaminan yang benar-benar kamu pakai untuk buka posisi (sebelum dikali leverage).",
    "Partial Fill": "Saat order market cuma terisi sebagian (bukan 100%) karena likuiditas di order book kurang. Bot punya proteksi otomatis untuk kasus ini.",
    "Rebalance": "Proses menyamakan saldo antara Bybit dan KuCoin, supaya bot selalu punya modal cukup di kedua sisi untuk buka posisi delta-neutral.",
    "Blacklist (Delisting)": "Daftar simbol yang diblokir dari entry baru karena terdeteksi bakal di-delist dari salah satu exchange.",
    "Paper Mode": "Mode simulasi — semua trade pakai saldo virtual, tidak ada dana real yang bergerak. Cocok untuk belajar/testing.",
    "Live Mode": "Mode real — bot benar-benar buka/tutup posisi pakai dana asli di exchange.",
}


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args if context.args else []
    if args and args[0].lower() == "glossary":
        lines = [b("📖 GLOSARIUM — Istilah di FR Bot"), ""]
        for term, explanation in GLOSSARY.items():
            lines.append(f"{b(term)}\n{explanation}\n")
        lines.append(i("Balik ke daftar command: /help"))
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    msg = (
        f"🤖 {b('FR Bot — Funding Rate Arbitrage')}\n\n"
        f"Bot mencari selisih Funding Rate antara Bybit dan KuCoin (Delta Neutral Arbitrage). "
        f"Short pada pair dengan funding rate tinggi, Long pada yang rendah.\n\n"
        f"📡 {b('Scan &amp; Analisa')}\n"
        f"<code>/scan</code> — Scan funding rate terbaru Bybit &amp; KuCoin. Tampilkan Top 5 by Diff FR &amp; Top 5 by APR.\n"
        f"<code>/top [N]</code> — Daftar Top N pair berdasarkan Funding Difference terbesar (default 10).\n"
        f"<code>/pair SYM</code> — Detail satu pair: price spread, funding rate, countdown, APR, dll. Contoh: <code>/pair BTC</code>.\n\n"
        f"💼 {b('Trading Manual')}\n"
        f"<code>/execute SYM [amount] [lev]</code> — Buka posisi manual. Contoh: <code>/execute TAIKO 100 3</code>.\n"
        f"<code>/close ID</code> — Tutup satu posisi berdasarkan ID.\n"
        f"<code>/closeall</code> — Tutup SEMUA posisi (tombol darurat).\n\n"
        f"📊 {b('Info &amp; Status')}\n"
        f"<code>/status</code> — Dashboard: mode, saldo, koneksi API, status Auto Engine.\n"
        f"<code>/portfolio</code> — Posisi terbuka detail (entry price, likuidasi, next payment tiap exchange, arah, PnL).\n"
        f"<code>/pnl</code> — Ringkasan Untung/Rugi (1D, 7D, 30D, Total).\n"
        f"<code>/mode</code> — Cek mode bot: PAPER (simulasi) atau LIVE (real).\n"
        f"<code>/health</code> — Test koneksi &amp; ping latency ke Bybit dan KuCoin.\n\n"
        f"🤖 {b('Automation (Auto-Trade)')}\n"
        f"<code>/auto on</code> — Aktifkan automation. Bot cari peluang &amp; eksekusi otomatis.\n"
        f"<code>/auto off</code> — Matikan automation (posisi berjalan tetap aman).\n"
        f"<code>/auto status</code> — Cek status mesin automation saat ini.\n\n"
        f"⚖️ {b('Rebalance')}\n"
        f"<code>/rebalance</code> — Cek saldo kedua exchange &amp; status keseimbangan.\n"
        f"<code>/rebalance on</code> — Aktifkan auto rebalance.\n"
        f"<code>/rebalance off</code> — Nonaktifkan auto rebalance.\n\n"
        f"🛠️ {b('Dasar')}\n"
        f"<code>/start</code> — Pesan sambutan / pengenalan.\n"
        f"<code>/help</code> — Bantuan lengkap semua command (ini).\n"
        f"<code>/help glossary</code> — Penjelasan istilah-istilah yang dipakai bot (delta-neutral, APR, dll) — {i('cocok buat yang baru mulai')}.\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
