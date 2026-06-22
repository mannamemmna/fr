"""/portfolio — Balances with Bybit/KuCoin split + open positions detail."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.scanner import read_opportunities
import handlers.state as state


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap. Coba lagi sebentar.")
        return

    summary = state.paper_engine.get_summary()
    positions = summary["positions"]

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

    opps = state.last_scan.get("opportunities", [])
    if not opps:
        opps = read_opportunities().get("opportunities", [])

    for p in positions:
        pid = p["id"][:8]
        sym = p["symbol"]
        margin = p["amount_usd"]
        lev = p.get("leverage", 1)
        pos_size = margin * lev
        spread = p.get("entry_spread", "—")
        side_bb = p["side_bybit"].upper()
        side_kc = p["side_kucoin"].upper()
        entry_bb = p.get("entry_price_bybit", 0)
        entry_kc = p.get("entry_price_kucoin", 0)

        dir_explain = "Jual di Bybit / Beli di KuCoin" if side_bb == "SELL" else "Beli di Bybit / Jual di KuCoin"
        price_label = f"Entry: Bybit `${entry_bb:.4f}` | KuCoin `${entry_kc:.4f}`"

        if lev and lev > 0:
            liq_bb = entry_bb * (1 + 1.0 / lev) if side_bb == "SELL" else entry_bb * (1 - 1.0 / lev)
            liq_kc = entry_kc * (1 + 1.0 / lev) if side_kc == "SELL" else entry_kc * (1 - 1.0 / lev)
            liq_label = f"Likuidasi: Bybit ~`${liq_bb:.4f}` / KuCoin ~`${liq_kc:.4f}`"
        else:
            liq_label = "Likuidasi: —"

        funding = p.get("funding_pnl", 0.0)
        
        # Ambil jam next payment dari data scan
        next_funding_jam = "—"
        upnl = "—"
        for o in opps:
            if o["symbol"].upper() == sym.upper():
                next_funding_jam = o.get("next_funding", "—")
                exit_bb = o.get("bybit_mark") or 0
                exit_kc = o.get("kucoin_mark") or 0
                qty = p.get("quantity", 0)
                pnl_bb = qty * (entry_bb - exit_bb) if side_bb == "SELL" else qty * (exit_bb - entry_bb)
                pnl_kc = qty * (entry_kc - exit_kc) if side_kc == "SELL" else qty * (exit_kc - entry_kc)
                upnl = f"`{(pnl_bb + pnl_kc):+.2f}`"
                break

        funding_label = f"Funding: ⌛ Next payment {next_funding_jam} | Diterima: `{funding:+.2f}` USD"

        spread_str = f"{spread}%" if isinstance(spread, float) else str(spread)
        lines.append(
            f"🪙 *{sym}* `{pid}`\n"
            f"├─ Margin: `${margin:.0f}` × {lev}x = *${pos_size:.0f}*\n"
            f"├─ Arah: {dir_explain}\n"
            f"├─ {price_label}\n"
            f"├─ {liq_label}\n"
            f"├─ {funding_label}\n"
            f"├─ Selisih FR saat entry: `{spread_str}`\n"
            f"├─ Profit/Loss saat ini: {upnl} USD\n"
            f"└─ Tutup: /close {pid}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
