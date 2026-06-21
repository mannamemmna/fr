"""/pnl — P&L summary with 1D/7D/30D breakdown."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from handlers.state import paper_engine


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = paper_engine.get_summary()
    closed = paper_engine.get_closed_positions()

    now = datetime.now(timezone.utc)
    pnl_1d = 0.0
    pnl_7d = 0.0
    pnl_30d = 0.0
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

    lines = [
        f"*💰 P&L SUMMARY*\n",
        f"📅 PnL 1D: `{pnl_1d:+.2f}` | 7D: `{pnl_7d:+.2f}` | 30D: `{pnl_30d:+.2f}`\n",
        f"Balance: `${summary['balance']:.2f}`\n",
        f"Realized PnL: `{summary['realized_pnl']:+.2f} USD`\n"
        f"Unrealized PnL: `{summary['unrealized_pnl']:+.2f} USD`\n"
        f"Total PnL: `{summary['total_pnl']:+.2f} USD`\n",
        f"Fees paid: `{summary['total_fees']:.2f} USD`\n"
        f"Est. Funding earned: `{summary['total_funding_pnl']:.2f} USD`\n",
    ]

    if closed:
        lines.append("*Last 5 closed trades:*")
        for p in closed[-5:]:
            sym = p["symbol"]
            pnl = p.get("realized_pnl", 0)
            total_fee = p.get("total_fee", 0)
            total_price_pnl = p.get("total_price_pnl", 0)
            funding = p.get("funding_pnl", 0)
            sign = "✅" if pnl >= 0 else "❌"
            lines.append(
                f"{sign} *{sym}*  PnL: `{pnl:+.2f}`  "
                f"(Price: `{total_price_pnl:+.2f}` "
                f"Funding: `{funding:+.2f}` "
                f"Fees: `-{total_fee:.2f}`)"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
