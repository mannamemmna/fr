"""/portfolio — Balances with Bybit/KuCoin split + open positions detail."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from core.scanner import read_opportunities
from handlers.state import paper_engine, last_scan


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PAPER_MODE:
        summary = paper_engine.get_summary()
        positions = summary["positions"]
    else:
        await update.message.reply_text("🔴 Live portfolio not yet implemented.")
        return

    total_exposure = summary.get("total_exposure", 0)
    bybit_balance = total_exposure
    kucoin_balance = total_exposure

    if not positions:
        await update.message.reply_text(
            f"📭 *No open positions*\n\n"
            f"💰 Balance: `${summary['balance']:.2f}`\n"
            f"🔹 Bybit Balance: `${bybit_balance:.2f}`\n"
            f"🔸 KuCoin Balance: `${kucoin_balance:.2f}`\n"
            f"📊 Realized PnL: `{summary['realized_pnl']:+.2f}`\n"
            f"📈 Total PnL: `{summary['total_pnl']:+.2f}`",
            parse_mode="Markdown",
        )
        return

    lines = [
        f"*📊 PORTFOLIO*\n"
        f"💰 Balance: `${summary['balance']:.2f}`  "
        f"Exposure: `${total_exposure:.2f}`\n"
        f"🔹 Bybit Balance: `${bybit_balance:.2f}`  "
        f"🔸 KuCoin Balance: `${kucoin_balance:.2f}`\n"
        f"📊 Realized: `{summary['realized_pnl']:+.2f}`  "
        f"Unrealized: `{summary['unrealized_pnl']:+.2f}`\n"
        f"📈 Total PnL: `{summary['total_pnl']:+.2f}`\n",
        f"*Open Positions ({len(positions)}):*",
    ]

    for p in positions:
        pid = p["id"][:10]
        sym = p["symbol"]
        margin = p["amount_usd"]
        lev = p.get("leverage", "?")
        pos_size = p.get("position_size", margin)
        spread = p.get("entry_spread", "—")
        dir_str = f"{p['side_bybit'].upper()} BB / {p['side_kucoin'].upper()} KC"

        entry_bb = p.get("entry_price_bybit", 0)
        entry_kc = p.get("entry_price_kucoin", 0)
        entry_prices = f"BB: ${entry_bb:.4f} // KC: ${entry_kc:.4f}"

        if p["side_bybit"] == "buy":
            liq_bb = entry_bb * (1 - 1.0 / lev)
        else:
            liq_bb = entry_bb * (1 + 1.0 / lev)
        if p["side_kucoin"] == "buy":
            liq_kc = entry_kc * (1 - 1.0 / lev)
        else:
            liq_kc = entry_kc * (1 + 1.0 / lev)
        liq_str = f"Liq: ${liq_bb:.4f}/${liq_kc:.4f}"

        funding = p.get("funding_pnl")
        if funding is not None:
            funding_str = f"Funding: `${funding:+.2f}`"
        else:
            funding_str = "Funding: ⌛ pending"

        # unrealized PnL
        upnl = "—"
        global last_scan
        if not last_scan:
            last_scan = read_opportunities()
        for o in last_scan.get("opportunities", []):
            if o["symbol"].upper() == sym.upper():
                exit_bb = o.get("bybit_mark") or o.get("price", 0)
                exit_kc = o.get("kucoin_mark") or o.get("price", 0)
                qty = p.get("quantity", 0)
                if p["side_bybit"] == "buy":
                    pnl_bb = qty * (exit_bb - entry_bb)
                else:
                    pnl_bb = qty * (entry_bb - exit_bb)
                if p["side_kucoin"] == "buy":
                    pnl_kc = qty * (exit_kc - entry_kc)
                else:
                    pnl_kc = qty * (entry_kc - exit_kc)
                upnl = f"`{(pnl_bb + pnl_kc):+.2f}`"
                break

        lines.append(
            f"`{pid}…` *{sym}* — Margin: `${margin:.0f}` × {lev}x = `${pos_size:.0f}`\n"
            f"  {dir_str}  |  Spread: `{spread}%`  |  uPnL: {upnl}\n"
            f"  {entry_prices}\n"
            f"  {liq_str}  |  {funding_str}\n"
            f"  _Close: /close {pid}_"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
