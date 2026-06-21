"""/close POS_ID + /closeall — Close paper positions."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from handlers.state import paper_engine


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: `/close <position_id>`\nFind IDs with /portfolio",
            parse_mode="Markdown",
        )
        return

    pos_id = context.args[0]
    result = paper_engine.close_position(pos_id)

    if result.get("ok"):
        await update.message.reply_text(
            f"✅ *Position closed!*\n\n"
            f"ID: `{pos_id[:12]}…`\n"
            f"Symbol: *{result.get('symbol', '?')}*\n"
            f"Realized PnL: `{result.get('realized_pnl', 0):+.2f} USD`\n"
            f"  Price PnL: `{result.get('price_pnl', 0):+.2f}`\n"
            f"  Funding: `{result.get('funding_pnl', 0):+.2f}`\n"
            f"  Fees: `{result.get('fees', 0):.2f}`\n\n"
            f"Bal: `${paper_engine.get_balance():.2f}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ {result.get('error', 'unknown error')}")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = paper_engine.get_open_positions()
    if not positions:
        await update.message.reply_text("📭 No open positions.")
        return

    await update.message.reply_text(f"🔄 Closing {len(positions)} positions…")
    results = paper_engine.close_all_positions()

    total_pnl = sum(r.get("realized_pnl", 0) for r in results)
    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok

    await update.message.reply_text(
        f"✅ Closed {ok}/{len(results)} positions\n"
        f"❌ Failed: {fail}\n"
        f"Total PnL: `{total_pnl:+.2f} USD`\n"
        f"Bal: `${paper_engine.get_balance():.2f}`",
        parse_mode="Markdown",
    )
