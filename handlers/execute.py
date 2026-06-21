"""/execute — Manual paper trade execution."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE, DEFAULT_LEVERAGE
from core.scanner import read_opportunities
from handlers.state import paper_engine, last_scan


async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Usage: `/execute <symbol> <amount_usd> [leverage]`\n"
            "Example: `/execute BTC 100` (default 2x)\n"
            "Example: `/execute ETH 50 3` (3x leverage)\n\n"
            "`amount_usd` = margin/collateral\n"
            "`leverage` = 1–20x (position = margin × leverage)\n"
            "Bot uses the direction from the latest scan automatically.",
            parse_mode="Markdown",
        )
        return

    symbol = context.args[0].upper()
    amount = 100.0
    leverage = DEFAULT_LEVERAGE

    if len(context.args) > 1:
        try:
            amount = float(context.args[1])
        except ValueError:
            await update.message.reply_text(f"Invalid amount: {context.args[1]}")
            return
    if len(context.args) > 2:
        try:
            leverage = int(context.args[2])
            leverage = max(1, min(leverage, 20))
        except ValueError:
            await update.message.reply_text(f"Invalid leverage: {context.args[2]}")
            return

    global last_scan
    if not last_scan:
        last_scan = read_opportunities()

    opp = None
    for o in last_scan.get("opportunities", []):
        if o["symbol"].upper() == symbol:
            opp = o
            break

    if not opp:
        await update.message.reply_text(
            f"❌ Symbol `{symbol}` not found in latest scan. Run /scan first.",
            parse_mode="Markdown",
        )
        return

    bybit_action = opp["bybit_action"]
    kucoin_action = opp["kucoin_action"]
    if bybit_action == "—":
        await update.message.reply_text(f"⚠️ Spread is flat for {symbol}, no trade.")
        return

    side_bb = "sell" if bybit_action == "SHORT" else "buy"
    side_kc = "sell" if kucoin_action == "SHORT" else "buy"

    result = paper_engine.execute_instant(symbol, amount, side_bb, side_kc, leverage)

    if result["status"] == "done":
        pos = result.get("position", {})
        lev = pos.get("leverage", leverage)
        pos_size = pos.get("position_size", amount * leverage)
        await update.message.reply_text(
            f"✅ *Order executed!*\n\n"
            f"ID: `{result['task_id'][:12]}…`\n"
            f"Symbol: *{symbol}*\n"
            f"Margin: `${amount:.0f}` × {lev}x = `${pos_size:.0f}` position\n"
            f"Direction: {opp['direction']}\n"
            f"Spread: `{opp['spread_pct']:+.4f}%`\n"
            f"APR: `{opp['annual_pct']:+.1f}%`\n\n"
            f"Bal: `${paper_engine.get_balance():.2f}`",
            parse_mode="Markdown",
        )
    else:
        errors = "\n".join(result.get("errors", ["unknown"]))
        await update.message.reply_text(f"❌ Failed:\n{errors}")
