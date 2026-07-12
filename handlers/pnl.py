"""/pnl — P&L summary with 1D/7D/30D breakdown."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from core.tg_format import b, code, esc
import handlers.state as state


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    summary = state.paper_engine.get_summary()
    closed = state.paper_engine.get_closed_positions()

    now = datetime.now(timezone.utc)
    pnl_1d = pnl_7d = pnl_30d = 0.0
    for p in closed:
        closed_at_str = p.get("exit_time") or p.get("closed_at", "")
        if not closed_at_str:
            continue
        try:
            closed_at = datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        rpnl = p.get("realized_pnl", 0)
        delta = now - closed_at
        if delta <= timedelta(days=1):
            pnl_1d += rpnl
        if delta <= timedelta(days=7):
            pnl_7d += rpnl
        if delta <= timedelta(days=30):
            pnl_30d += rpnl

    bal_s = f"${summary['balance']:.2f}"
    real_s = f"{summary['realized_pnl']:+.2f} USD"
    unreal_s = f"{summary['unrealized_pnl']:+.2f} USD"
    total_s = f"{summary['total_pnl']:+.2f} USD"
    fees_s = f"{summary['total_fees']:.2f} USD"
    fund_s = f"{summary['total_funding_pnl']:.2f} USD"

    lines = [
        f"{b('💰 P&amp;L SUMMARY')}\n",
        f"📅 1 Hari: {code(f'{pnl_1d:+.2f}')} | 7 Hari: {code(f'{pnl_7d:+.2f}')} | 30 Hari: {code(f'{pnl_30d:+.2f}')}\n",
        f"Saldo: {code(bal_s)}\n",
        f"Sudah direalisasi: {code(real_s)}\n"
        f"Belum direalisasi: {code(unreal_s)}\n"
        f"Total PnL: {code(total_s)}\n",
        f"Total biaya: {code(fees_s)}\n"
        f"Est. funding diterima: {code(fund_s)}\n",
    ]

    if closed:
        lines.append(b("5 Trade Terakhir:"))
        for p in closed[-5:]:
            sym = esc(p["symbol"])
            pnl = p.get("realized_pnl", 0)
            total_fee = p.get("total_fee", 0)
            price_pnl = p.get("total_price_pnl", 0)
            funding = p.get("funding_pnl", 0)
            fr_paid = p.get("fr_paid", 0)
            fr_received = p.get("fr_received", 0)
            sign = "✅" if pnl >= 0 else "❌"
            lines.append(
                f"{sign} {b(sym)}  PnL: {code(f'{pnl:+.2f}')}  "
                f"(Harga: {code(f'{price_pnl:+.2f}')} | FR terima: {code(f'{fr_received:.2f}')} | FR bayar: {code(f'{fr_paid:.2f}')} | Fee: {code(f'{total_fee:.2f}')})"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
