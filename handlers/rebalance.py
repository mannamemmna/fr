"""Handler Telegram untuk command /rebalance."""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes

import handlers.state as state


def _fmt_time(ts: float) -> str:
    if ts <= 0:
        return "— (belum pernah)"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(timezone(timedelta(hours=7)))
    return dt.strftime("%H:%M WIB")


async def cmd_rebalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.rebalance_engine:
        await update.message.reply_text("⚠️ Rebalance engine belum diinisialisasi.")
        return

    args = context.args
    eng = state.rebalance_engine

    if args and args[0].lower() == "on":
        eng.enable()
        await update.message.reply_text("🟢 Auto Rebalancing ON")
        return

    if args and args[0].lower() == "off":
        eng.disable()
        await update.message.reply_text("🔴 Auto Rebalancing OFF")
        return

    if args and args[0].lower() == "log":
        log = eng.get_rebalance_log(10)
        if not log:
            await update.message.reply_text("📭 Belum ada aksi rebalancing.")
            return
        lines = ["*⚖️ Rebalance Log (10 terakhir)*", ""]
        for entry in reversed(log):
            ts = entry.get("ts", 0)
            act = entry.get("action", "?")
            trig = entry.get("trigger", "?")
            sym = entry.get("symbol", "?")
            ok = entry.get("ok", False)
            icon = "✅" if ok else "❌"
            lines.append(f"{icon} `{_fmt_time(ts)}` — {act} ({trig}) — {sym}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if args and args[0].lower() == "now":
        positions = state.paper_engine.get_open_positions() if state.paper_engine else []
        if not positions:
            await update.message.reply_text("📭 Tidak ada posisi terbuka.")
            return
        results = []
        for pos in positions:
            r = eng.check_and_rebalance(
                position=pos,
                paper_engine=state.paper_engine,
                notify_fn=lambda t, m: None,
            )
            if r:
                results.append(f"{pos['symbol']}: {r.get('action', '?')} — {r.get('ok', False)}")
        if results:
            await update.message.reply_text("⚙️ Rebalance manual:\n" + "\n".join(results))
        else:
            await update.message.reply_text("✅ Semua posisi dalam kondisi seimbang.")
        return

    # Default: status
    status = eng.get_status()
    enabled = status.get("enabled", False)
    last_ts = status.get("last_rebalance_ts", 0)
    cooldown = status.get("cooldown_remaining", 0)
    drift_pct = status.get("threshold_pct", 5.0)
    margin_min = status.get("margin_min_pct", 15)
    margin_emerg = status.get("margin_emergency_pct", 5)
    cd_sec = status.get("cooldown_sec", 60)

    lines = [
        "⚖️ AUTO REBALANCING",
        "",
        f"Status: {'🟢 ON' if enabled else '🔴 OFF'}",
        f"Delta threshold: {drift_pct}%",
        f"Margin safety: {margin_min:.0f}% (emergency: {margin_emerg:.0f}%)",
        f"Cooldown: {cd_sec}s",
        "",
    ]

    # Drift per position
    if state.paper_engine:
        positions = state.paper_engine.get_open_positions()
        for pos in positions:
            pid = pos["id"][:8]
            sym = pos["symbol"]
            qty_bb = float(pos.get("qty_bybit", 0) or pos.get("quantity", 0))
            qty_kc = float(pos.get("qty_kucoin", 0) or pos.get("quantity", 0))
            entry_bb = float(pos.get("entry_price_bybit", 0))
            entry_kc = float(pos.get("entry_price_kucoin", 0))
            bb_notional = qty_bb * entry_bb
            kc_notional = qty_kc * entry_kc
            avg = (bb_notional + kc_notional) / 2
            drift = abs(bb_notional - kc_notional) / max(avg, 0.0001) * 100.0

            margin_bb = pos.get("amount_usd", 0) / max(bb_notional, 0.0001)
            margin_kc = pos.get("amount_usd", 0) / max(kc_notional, 0.0001)

            drift_ok = drift < drift_pct
            drift_icon = "✅" if drift_ok else "⚠️"
            lines.append(
                f"📊 Posisi *{sym}* (`{pid}`)\n"
                f"├ BB notional: `${bb_notional:.2f}` | KC notional: `${kc_notional:.2f}`\n"
                f"├ Delta drift: `{drift:.2f}%` (threshold: `{drift_pct}%`) {drift_icon}\n"
                f"├ Margin BB: `{margin_bb*100:.1f}%` | Margin KC: `{margin_kc*100:.1f}%`\n"
                f"└ Last rebalance: `{_fmt_time(last_ts)}`"
            )

    # Exchange balances
    if state.paper_engine:
        bb_bal = state.paper_engine.get_bybit_balance()
        kc_bal = state.paper_engine.get_kucoin_balance()
        lines += [
            "",
            "💰 *Exchange Balance (Paper)*",
            f"├ Bybit: `${bb_bal:.2f}`",
            f"└ KuCoin: `${kc_bal:.2f}`",
        ]

    # Cooldown indicator
    if cooldown > 0:
        lines += ["", f"⏳ Cooldown tersisa: `{cooldown:.0f}s`"]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")