from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from core.scanner import run_scan
from core.tg_format import b, i, code, esc
import handlers.state as state

log = logging.getLogger("fr-bot.scan")


def _format_opp(o: dict, rank: int = 0) -> str:
    prefix = f"#{rank} " if rank else ""
    symbol = o["symbol"]
    spread = o["spread_pct"]
    dir_short = esc(o["direction"])
    apr = o["annual_pct"]
    funding_diff = o.get("funding_diff_pct", 0)
    bb_rate = o.get("bybit_rate_pct", 0)
    kc_rate = o.get("kucoin_rate_pct", 0)
    bb_time = esc(o.get("bybit_next_time", "—"))
    kc_time = esc(o.get("kucoin_next_time", "—"))
    pos = "+" if spread >= 0 else ""
    emoji = "🟢" if apr > 500 else "🟡" if apr > 200 else "⚪"
    title = f"{prefix}{symbol}"
    return (
        f"{emoji} {b(title)}  |  APR: {code(f'{apr:+.1f}%')}\n"
        f"   Diff: {code(f'{funding_diff:.4f}%')}  |  Price Spread: {code(f'{pos}{spread:.4f}%')}\n"
        f"   {dir_short}\n"
        f"   BB: {code(f'{bb_rate:+.4f}%')} ({bb_time})  KC: {code(f'{kc_rate:+.4f}%')} ({kc_time})"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning funding rates…")
    try:
        payload = run_scan()
        state.last_scan = payload

        # Subscribe WebSocket to all common symbols so next calls are real-time
        symbols = [o["symbol"] for o in payload.get("opportunities", [])]
        if symbols and state.ws_pool:
            state.ws_pool.update_symbols(symbols)
            log.debug("/scan: WS subscribed to %d symbols", len(symbols))

        opps = payload["opportunities"]
        dur = payload["scan_duration"]
        bb = payload["bybit_count"]
        kc = payload["kucoin_count"]
        common = payload["common_count"]
        # Sort Top 5 by Funding Diff
        top_diff = sorted(opps, key=lambda o: o.get("funding_diff_pct", 0), reverse=True)[:5]
        top_diff_str = "\n\n".join(_format_opp(o, idx + 1) for idx, o in enumerate(top_diff))

        # Sort Top 5 by APR
        top_apr = sorted(opps, key=lambda o: o.get("annual_pct", 0), reverse=True)[:5]
        top_apr_str = "\n\n".join(_format_opp(o, idx + 1) for idx, o in enumerate(top_apr))

        scan_title = f"Scan complete in {dur:.1f}s"
        await msg.edit_text(
            f"✅ {b(scan_title)}\n"
            f"Bybit: {bb} pairs | KuCoin: {kc} pairs | Common: {common}\n\n"
            f"{b('🏆 TOP 5 BY FUNDING DIFF')}\n\n{top_diff_str}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{b('🔥 TOP 5 BY APR')}\n\n{top_apr_str}\n\n"
            f"{i('Istilah belum familiar? Ketik /help glossary untuk penjelasan.')}",
            parse_mode="HTML",
        )
    except Exception as e:
        await msg.edit_text(f"❌ Scan failed: {esc(str(e))}")
