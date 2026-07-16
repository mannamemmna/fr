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
from core.tg_format import b, i, code, esc


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
            f"📋 {b('Usage:')} <code>/pair &lt;symbol&gt;</code>\n\n"
            f"Contoh: <code>/pair WAXP</code> atau <code>/pair BTC</code>",
            parse_mode="HTML",
        )
        return

    raw_symbol = " ".join(context.args).upper().strip()
    # Literal suffix removal, not rstrip() -- rstrip("USDT") strips a
    # CHARACTER SET (any trailing U/S/D/T), which mangles base symbols that
    # themselves end in one of those letters. "DOTUSDT" would become "DO"
    # (loses the T twice over) instead of "DOT"; "GASUSDT" would become "GA"
    # instead of "GAS". removesuffix() only strips the literal substring.
    raw_symbol_base = raw_symbol.removesuffix("USDT")
    data = read_opportunities()
    opps = data.get("opportunities", [])

    # Cari: cocok dengan symbol (base) atau unified_symbol
    match = None
    for o in opps:
        sym = o["symbol"].upper()
        uni = o.get("unified_symbol", "").upper()
        if sym == raw_symbol or sym == raw_symbol_base or uni == raw_symbol or \
           uni.startswith(raw_symbol) or raw_symbol in sym:
            match = o
            break

    if not match:
        await update.message.reply_text(
            f"❌ Pair {code(raw_symbol)} tidak ditemukan di scan terakhir.\n"
            f"Gunakan <code>/scan</code> dulu untuk refresh data.",
            parse_mode="HTML",
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
        f"📊 {b(f'{symbol} Detail')}\n"
        f"└ Unified: {code(uni)}\n\n"
        f"═══ {b('PRICE')} ═══\n"
        f"├ Bybit: {code(f'${bb_price:.6f}')}\n"
        f"└ KuCoin: {code(f'${kc_price:.6f}')}\n\n"
        f"═══ {b('SPREAD')} ═══\n"
        f"├ Price Spread: {code(f'{spread_sign}{spread:.6f}%')}\n"
        f"└ Arah: {code(bybit_action)} Bybit / {code(kucoin_action)} KuCoin\n\n"
        f"═══ {b('FUNDING RATE')} ═══\n"
        f"├ Bybit: {code(f'{bb_rate:+.6f}%')}  ({bb_iv}h / next: {bb_next_time})\n"
        f"├ KuCoin: {code(f'{kc_rate:+.6f}%')}  ({kc_iv}h / next: {kc_next_time})\n"
        f"├ Next Pay Bybit: {code(f'{bb_next_pay:+.6f}%')}\n"
        f"├ Next Pay KuCoin: {code(f'{kc_next_pay:+.6f}%')}\n"
        f"└ Countdown: BB {code(bb_ct)}  KC {code(kc_ct)}\n\n"
        f"═══ {b('DELTA')} ═══\n"
        f"├ Raw FR Diff: {code(f'{raw_fr_diff:+.6f}%')}\n"
        f"├ Normalized Diff: {code(f'{funding_diff:.6f}%')}\n"
        f"├ Diff Daily: {code(f'{diff_daily:.4f}%')}\n"
        f"├ Net Daily: {code(f'{net_daily:.4f}%')}\n"
        f"└ Annual APR: {code(f'{annual:.2f}%')}\n\n"
        f"📌 {b('Direction:')} {code(direction)}\n\n"
        f"{i('Istilah belum familiar? Ketik /help glossary untuk penjelasan.')}"
    )

    await update.message.reply_text(msg, parse_mode="HTML")
