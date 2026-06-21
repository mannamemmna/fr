"""/portfolio — Balances with Bybit/KuCoin split + open positions detail."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from core.scanner import read_opportunities
from handlers.state import paper_engine, last_scan


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_scan
    if PAPER_MODE:
        summary = paper_engine.get_summary()
        positions = summary["positions"]
    else:
        await update.message.reply_text("🔴 Live portfolio belum tersedia.")
        return

    balance = summary.get("balance", 0)
    total_pnl = summary.get("total_pnl", 0)
    realized = summary.get("realized_pnl", 0)
    unrealized = summary.get("unrealized_pnl", 0)
    total_exposure = summary.get("total_exposure", 0)

    if not positions:
        await update.message.reply_text(
            f"📭 *Tidak ada posisi terbuka*\n\n"
            f"💰 Saldo: `${balance:.2f}`\n"
            f"📈 Total PnL: `{total_pnl:+.2f}`",
            parse_mode="Markdown",
        )
        return

    lines = [
        f"*💼 Portfolio*",
        "",
        f"💰 *Saldo:* `${balance:.2f}`",
        f"📊 *Terpakai:* `${total_exposure:.2f}` (sebagai margin)",
        f"📈 *Total PnL:* `{total_pnl:+.2f}`",
        f"   ├─ Sudah direalisasi: `{realized:+.2f}`",
        f"   └─ Belum direalisasi: `{unrealized:+.2f}`",
        "",
        f"*Posisi Terbuka ({len(positions)})*",
        "",
    ]

    for p in positions:
        pid = p["id"][:8]
        sym = p["symbol"]
        margin = p["amount_usd"]
        lev = p.get("leverage", "?")
        pos_size = margin * lev
        spread = p.get("entry_spread", "—")
        side_bb = p["side_bybit"].upper()
        side_kc = p["side_kucoin"].upper()
        entry_bb = p.get("entry_price_bybit", 0)
        entry_kc = p.get("entry_price_kucoin", 0)

        # Direction explanation
        if side_bb == "SELL":
            dir_explain = f"Jual di Bybit / Beli di KuCoin"
        else:
            dir_explain = f"Beli di Bybit / Jual di KuCoin"

        # Entry price label
        price_label = f"Entry: Bybit ${entry_bb:.4f} | KuCoin ${entry_kc:.4f}"

        # Liq price
        if side_bb == "BUY":  # Long
            liq_bb = entry_bb * (1 - 1.0 / lev)
        else:  # Short
            liq_bb = entry_bb * (1 + 1.0 / lev)
        if side_kc == "BUY":
            liq_kc = entry_kc * (1 - 1.0 / lev)
        else:
            liq_kc = entry_kc * (1 + 1.0 / lev)
        liq_label = f"Likuidasi: Bybit ~${liq_bb:.4f} / KuCoin ~${liq_kc:.4f}"

        # Funding
        funding = p.get("funding_pnl")
        if funding is not None:
            funding_label = f"Funding diterima: `{funding:+.2f}` USD"
        else:
            funding_label = "Funding: ⌛ menunggu pembayaran"

        # uPnL
        upnl = "—"
        if not last_scan:
            last_scan = read_opportunities()
        for o in last_scan.get("opportunities", []):
            if o["symbol"].upper() == sym.upper():
                exit_bb = o.get("bybit_mark") or 0
                exit_kc = o.get("kucoin_mark") or 0
                qty = p.get("quantity", 0)
                if side_bb == "BUY":
                    pnl_bb = qty * (exit_bb - entry_bb)
                else:
                    pnl_bb = qty * (entry_bb - exit_bb)
                if side_kc == "BUY":
                    pnl_kc = qty * (exit_kc - entry_kc)
                else:
                    pnl_kc = qty * (entry_kc - exit_kc)
                upnl = f"`{(pnl_bb + pnl_kc):+.2f}`"
                break

        lines.append(
            f"🪙 *{sym}* `{pid}`\n"
            f"├─ Margin: `${margin:.0f}` × {lev}x = *${pos_size:.0f}*\n"
            f"├─ Arah: {dir_explain}\n"
            f"├─ {price_label}\n"
            f"├─ {liq_label}\n"
            f"├─ {funding_label}\n"
            f"├─ Selisih FR saat entry: `{spread}%`\n"
            f"├─ Profit/Loss saat ini: {upnl} USD\n"
            f"└─ Tutup: /close {pid}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
