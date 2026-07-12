"""/health — Exchange connectivity check with ping latency."""

from __future__ import annotations
import time

from telegram import Update
from telegram.ext import ContextTypes

from core.tg_format import b, code, esc
import handlers.state as state


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"{b('🏥 EXCHANGE HEALTH')}\n"]
    for name in ("bybit", "kucoin"):
        try:
            t0 = time.time()
            client = __import__("exchanges", fromlist=["get_client"]).get_client(name)
            client.fetch_all_funding_rates()
            latency = (time.time() - t0) * 1000
            state.exchange_health[name] = True
            lines.append(f"🟢 {b(name.upper())} — {latency:.0f}ms")
        except Exception as e:
            state.exchange_health[name] = False
            lines.append(f"🔴 {b(name.upper())} — DOWN: {code(str(e))}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
