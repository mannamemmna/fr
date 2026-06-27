"""/pair <symbol> — Detail satu pair funding rate di kedua CEX.

Format output:
- Price Bybit & KuCoin
- Price Spread
- Funding diff (normalized)
- Funding rate BB & KC (aktual)
- Next funding time BB & KC (WIB)
- Interval BB & KC
- Next payment rate BB & KC
- APR tahunan
- Arah / direction
- Raw FR diff (belum dinormalisasi)
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.scanner import read_opportunities


def _fmt_wib_from_ts(ts_sec: int | None) -> str:
    """Format epoch seconds → 'HH:MM WIB'."""
    if not ts_sec:
        return "—"
    from datetime import datetime, timezone, timedelta
    dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    wib = dt.astimezone(timezone(timedelta(hours=7)))
    return wib.strftime("%H:%M WIB")


def _fmt_countdown(ts_sec: int | None) -> str:
    """Format epoch seconds → countdown string."""
    import time
    if not ts_sec:
        return "—"
    diff = ts_sec - time.time()
    if diff <= 0:
        return "🔴 Lewat"
    m, s = divmod(int(diff), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}j {m}m"
    return f"{m}m {s}s"


async def cmd_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "📋 *Usage:* `/pair <symbol>`\n\n"
            "Contoh: `/pair WAXP` atau `/pair BTC`",
            parse_mode="Markdown",
        )
        return

    raw_symbol = " ".join(context.args).upper().strip()
    data = read_opportunities()
    opps = data.get("opportunities", [])

    # Cari: cocok dengan symbol (base) atau unified_symbol
    match = None
    for o in opps:
        sym = o["symbol"].upper()
        uni = o.get("unified_symbol", "").upper()
        if sym == raw_symbol or sym == raw_symbol.rstrip("USDT") or uni == raw_symbol or \
           uni.startswith(raw_symbol) or raw_symbol in sym:
            match = o
            break

    if not match:
        await update.message.reply_text(
            f"❌ Pair `{raw_symbol}` tidak ditemukan di scan terakhir.\n"
            f"Gunakan `/scan` dulu untuk refresh data.",
            parse_mode="Markdown",
        )
        return

    # ── Ekstrak semua field ──
    def _g(key: str, default=None):
        return match.get(key, default)

    symbol = _g("symbol")
    uni = _g("unified_symbol")
    bb_price = _g("bybit_mark", 0) or 0
    kc_price = _g("kucoin_mark", 0) or 0
    spread = _g("spread_pct", 0)
    bb_rate = _g("bybit_rate_pct", 0)
    kc_rate = _g("kucoin_rate_pct", 0)
    bb_next_ts = _g("bybit_next_ts")
    kc_next_ts = _g("kucoin_next_ts")
    bb_iv = _g("bybit_interval_h", "?")
    kc_iv = _g("kucoin_interval_h", "?")
    direction = _g("direction", "—")
    bybit_action = _g("bybit_action", "—")
    kucoin_action = _g("kucoin_action", "—")
    funding_diff = _g("funding_diff_pct", 0)
    raw_fr_diff = _g("raw_fr_diff", 0)
    annual = _g("annual_pct", 0)
    net_daily = _g("net_daily_pct", 0)
    diff_daily = _g("diff_daily_pct", 0)
    bb_next_pay = _g("bybit_next_payment_pct", 0)
    kc_next_pay = _g("kucoin_next_payment_pct", 0)
    bb_next_time = _fmt_wib_from_ts(bb_next_ts)
    kc_next_time = _fmt_wib_from_ts(kc_next_ts)
    bb_ct = _fmt_countdown(bb_next_ts)
    kc_ct = _fmt_countdown(kc_next_ts)

    spread_sign = "+" if spread >= 0 else ""

    msg = (
        f"📊 *{symbol} Detail*\n"
        f"└ Unified: `{uni}`\n\n"
        f"═══ *PRICE* ═══\n"
        f"├ Bybit: `${bb_price:.6f}`\n"
        f"└ KuCoin: `${kc_price:.6f}`\n\n"
        f"═══ *SPREAD* ═══\n"
        f"├ Price Spread: `{spread_sign}{spread:.6f}%`\n"
        f"└ Arah: `{bybit_action}` Bybit / `{kucoin_action}` KuCoin\n\n"
        f"═══ *FUNDING RATE* ═══\n"
        f"├ Bybit: `{bb_rate:+.6f}%`  ({bb_iv}h / next: {bb_next_time})\n"
        f"├ KuCoin: `{kc_rate:+.6f}%`  ({kc_iv}h / next: {kc_next_time})\n"
        f"├ Next Pay Bybit: `{bb_next_pay:+.6f}%`\n"
        f"├ Next Pay KuCoin: `{kc_next_pay:+.6f}%`\n"
        f"└ Countdown: BB `{bb_ct}`  KC `{kc_ct}`\n\n"
        f"═══ *DELTA* ═══\n"
        f"├ Raw FR Diff: `{raw_fr_diff:+.6f}%`\n"
        f"├ Normalized Diff: `{funding_diff:.6f}%`\n"
        f"├ Diff Daily: `{diff_daily:.4f}%`\n"
        f"├ Net Daily: `{net_daily:.4f}%`\n"
        f"└ Annual APR: `{annual:.2f}%`\n\n"
        f"📌 *Direction:* `{direction}`"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")