"""/portfolio — Balances with Bybit/KuCoin split + open positions detail."""

from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
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
        bb = state.paper_engine.get_bybit_balance() if state.paper_engine else 0
        kc = state.paper_engine.get_kucoin_balance() if state.paper_engine else 0
        await update.message.reply_text(
            f"📭 *Tidak ada posisi terbuka*\n\n"
            f"💰 *Saldo:*\n"
            f"├─ Bybit:  `${bb:.2f}`\n"
            f"├─ KuCoin: `${kc:.2f}`\n"
            f"└─ Total:  `${bb+kc:.2f}`\n\n"
            f"📈 Total PnL: `{total_pnl:+.2f}`",
            parse_mode="Markdown",
        )
        return

    lines = [
        f"*💼 Portfolio*",
        "",
        f"💰 *Saldo:*",
        f"├─ Bybit:  `${summary.get('bybit_balance', 0):.2f}`",
        f"├─ KuCoin: `${summary.get('kucoin_balance', 0):.2f}`",
        f"└─ Total:  `${summary.get('balance', 0):.2f}`",
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
        fr_paid = p.get("fr_paid", 0.0)
        fr_received = p.get("fr_received", 0.0)

        # Ambil jam next payment masing-masing exchange dari data scan
        bybit_funding_jam = "—"
        kucoin_funding_jam = "—"
        upnl = "—"
        current_bb = "—"
        current_kc = "—"

        for o in opps:
            if o["symbol"].upper() == sym.upper():
                # Format waktu ke WIB (UTC+7)
                bb_ts = o.get("bybit_next_ts", 0) or 0
                kc_ts = o.get("kucoin_next_ts", 0) or 0

                if bb_ts > 0:
                    dt_utc = datetime.fromtimestamp(bb_ts, tz=timezone.utc)
                    dt_wib = dt_utc.astimezone(timezone(timedelta(hours=7)))
                    bybit_funding_jam = dt_wib.strftime("%H:%M WIB")
                else:
                    bybit_funding_jam = o.get("next_funding", "—").replace("UTC", "WIB")

                if kc_ts > 0:
                    dt_utc = datetime.fromtimestamp(kc_ts, tz=timezone.utc)
                    dt_wib = dt_utc.astimezone(timezone(timedelta(hours=7)))
                    kucoin_funding_jam = dt_wib.strftime("%H:%M WIB")
                else:
                    kucoin_funding_jam = o.get("next_funding", "—").replace("UTC", "WIB")

                # Ambil mark price terkini
                c_bb = o.get("bybit_mark", 0)
                c_kc = o.get("kucoin_mark", 0)
                if c_bb: current_bb = f"${c_bb:.4f}"
                if c_kc: current_kc = f"${c_kc:.4f}"
                break

        # Ambil uPnL secara langsung
        for o in opps:
            if o["symbol"].upper() == sym.upper():
                exit_bb = o.get("bybit_mark") or 0
                exit_kc = o.get("kucoin_mark") or 0
                qty = p.get("quantity", 0)
                pnl_bb = qty * (entry_bb - exit_bb) if side_bb == "SELL" else qty * (exit_bb - entry_bb)
                pnl_kc = qty * (entry_kc - exit_kc) if side_kc == "SELL" else qty * (exit_kc - entry_kc)
                upnl = f"`{(pnl_bb + pnl_kc):+.2f}`"
                break

        funding_label = f"Funding: ⌛ Next payment | Bybit: `{bybit_funding_jam}` | KuCoin: `{kucoin_funding_jam}`"
        fr_label = f"FR dibayar: `{fr_paid:.2f}` | diterima: `{fr_received:.2f}` | bersih: `{funding:+.2f}` USD"

        spread_str = f"{spread}%" if isinstance(spread, float) else str(spread)

        lines.append(
            f"🪙 *{sym}* `{pid}`\n"
            f"├─ Margin: `${margin:.0f}` × {lev}x = *${pos_size:.0f}*\n"
            f"├─ Arah: {dir_explain}\n"
            f"├─ {price_label}\n"
            f"├─ Harga Saat Ini: Bybit `{current_bb}` | KuCoin `{current_kc}`\n"
            f"├─ {liq_label}\n"
            f"├─ {funding_label}\n"
            f"├─ {fr_label}\n"
            f"├─ Price Spread saat entry: `{spread_str}`\n"
            f"├─ Profit/Loss saat ini: {upnl} USD\n"
            f"└─ Tutup: /close {pid}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
