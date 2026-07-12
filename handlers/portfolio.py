"""/portfolio — Balances with Bybit/KuCoin split + open positions detail."""

from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes

from core.scanner import read_opportunities
from core.tg_format import b, i, code, esc
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
        bb_s = f"${bb:.2f}"
        kc_s = f"${kc:.2f}"
        tot_s = f"${bb+kc:.2f}"
        await update.message.reply_text(
            f"📭 {b('Tidak ada posisi terbuka')}\n\n"
            f"💰 {b('Saldo:')}\n"
            f"├─ Bybit:  {code(bb_s)}\n"
            f"├─ KuCoin: {code(kc_s)}\n"
            f"└─ Total:  {code(tot_s)}\n\n"
            f"📈 Total PnL: {code(f'{total_pnl:+.2f}')}",
            parse_mode="HTML",
        )
        return

    bb_bal_s = f"${summary.get('bybit_balance', 0):.2f}"
    kc_bal_s = f"${summary.get('kucoin_balance', 0):.2f}"
    tot_bal_s = f"${summary.get('balance', 0):.2f}"

    lines = [
        b("💼 Portfolio"),
        "",
        f"💰 {b('Saldo:')}",
        f"├─ Bybit:  {code(bb_bal_s)}",
        f"├─ KuCoin: {code(kc_bal_s)}",
        f"└─ Total:  {code(tot_bal_s)}",
        f"📊 {b('Terpakai:')} {code(f'${total_exposure:.2f}')} (sebagai margin)",
        f"📈 {b('Total PnL:')} {code(f'{total_pnl:+.2f}')}",
        f"   ├─ Sudah direalisasi: {code(f'{realized:+.2f}')}",
        f"   └─ Belum direalisasi: {code(f'{unrealized:+.2f}')}",
        "",
        b(f"Posisi Terbuka ({len(positions)})"),
        "",
    ]

    opps = state.last_scan.get("opportunities", [])
    if not opps:
        opps = read_opportunities().get("opportunities", [])

    for p in positions:
        pid = p["id"][:8]
        sym = esc(p["symbol"])
        margin = p["amount_usd"]
        lev = p.get("leverage", 1)
        pos_size = margin * lev
        spread = p.get("entry_spread", "—")
        side_bb = esc(p["side_bybit"].upper())
        side_kc = esc(p["side_kucoin"].upper())
        entry_bb = p.get("entry_price_bybit", 0)
        entry_kc = p.get("entry_price_kucoin", 0)

        dir_explain = "Jual di Bybit / Beli di KuCoin" if p["side_bybit"].upper() == "SELL" else "Beli di Bybit / Jual di KuCoin"

        ebb_s = f"${entry_bb:.4f}"
        ekc_s = f"${entry_kc:.4f}"
        price_label = f"Entry: Bybit {code(ebb_s)} | KuCoin {code(ekc_s)}"

        if lev and lev > 0:
            liq_bb = entry_bb * (1 + 1.0 / lev) if p["side_bybit"].upper() == "SELL" else entry_bb * (1 - 1.0 / lev)
            liq_kc = entry_kc * (1 + 1.0 / lev) if p["side_kucoin"].upper() == "SELL" else entry_kc * (1 - 1.0 / lev)
            lbb_s = f"${liq_bb:.4f}"
            lkc_s = f"${liq_kc:.4f}"
            liq_label = f"Likuidasi: Bybit ~{code(lbb_s)} / KuCoin ~{code(lkc_s)}"
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
            if o["symbol"].upper() == p["symbol"].upper():
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

                c_bb = o.get("bybit_mark", 0)
                c_kc = o.get("kucoin_mark", 0)
                if c_bb: current_bb = f"${c_bb:.4f}"
                if c_kc: current_kc = f"${c_kc:.4f}"
                break

        for o in opps:
            if o["symbol"].upper() == p["symbol"].upper():
                exit_bb = o.get("bybit_mark") or 0
                exit_kc = o.get("kucoin_mark") or 0
                qty = p.get("quantity", 0)
                pnl_bb = qty * (entry_bb - exit_bb) if p["side_bybit"].upper() == "SELL" else qty * (exit_bb - entry_bb)
                pnl_kc = qty * (entry_kc - exit_kc) if p["side_kucoin"].upper() == "SELL" else qty * (exit_kc - entry_kc)
                upnl_val = f"{(pnl_bb + pnl_kc):+.2f}"
                upnl = code(upnl_val)
                break

        funding_label = f"Funding: ⌛ Next payment | Bybit: {code(bybit_funding_jam)} | KuCoin: {code(kucoin_funding_jam)}"
        fr_label = f"FR dibayar: {code(f'{fr_paid:.2f}')} | diterima: {code(f'{fr_received:.2f}')} | bersih: {code(f'{funding:+.2f}')} USD"

        spread_str = f"{spread}%" if isinstance(spread, float) else str(spread)
        margin_s = f"${margin:.0f}"
        size_s = f"${pos_size:.0f}"

        lines.append(
            f"🪙 {b(p['symbol'])} {code(pid)}\n"
            f"├─ Margin: {code(margin_s)} × {lev}x = {b(size_s)}\n"
            f"├─ Arah: {esc(dir_explain)}\n"
            f"├─ {price_label}\n"
            f"├─ Harga Saat Ini: Bybit {code(current_bb)} | KuCoin {code(current_kc)}\n"
            f"├─ {liq_label}\n"
            f"├─ {funding_label}\n"
            f"├─ {fr_label}\n"
            f"├─ Price Spread saat entry: {code(spread_str)}\n"
            f"├─ Profit/Loss saat ini: {upnl} USD\n"
            f"└─ Tutup: /close {pid}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
