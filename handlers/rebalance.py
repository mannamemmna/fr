"""/rebalance — Cek dan trigger manual rebalance saldo Bybit ↔ KuCoin."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
import handlers.state as state


async def cmd_rebalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /rebalance — show balance split and trigger if needed."""
    if not state.auto_engine:
        await update.message.reply_text("⚠️ Auto engine belum aktif.")
        return

    rb = state.auto_engine._rebalance_engine
    if not rb:
        await update.message.reply_text("⚠️ Rebalance engine belum diinisialisasi.")
        return

    # Parse subcommand
    args = context.args
    if args:
        sub = args[0].lower()
        if sub == "on":
            rb.toggle(True)
            await update.message.reply_text("🟢 Auto rebalance ON")
            return
        elif sub == "off":
            rb.toggle(False)
            await update.message.reply_text("🔴 Auto rebalance OFF")
            return
        elif sub == "transfers":
            from core.db import get_db
            db = get_db()
            transfers = db.get_recent_transfers(5)
            if not transfers:
                await update.message.reply_text("📭 Tidak ada riwayat transfer.")
                return
            lines = ["*📋 TRANSFER TERAKHIR (5)*"]
            for t in transfers:
                icon = "🟢" if t.get("type") == "withdraw_complete" else ("🔴" if "fail" in t.get("type", "") else "🟡")
                lines.append(f"{icon} `{t.get('client_id', '')[:12]}` | {t.get('from','?')}→{t.get('to','?')} | ${t.get('amount',0):.2f} | {t.get('type','?')}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

    status = rb.get_status()
    bb_pct = status["ratio_bybit"]
    kc_pct = status["ratio_kucoin"]

    mode_str = "📄 Paper (simulasi)" if PAPER_MODE else "🔴 Live"

    if status["is_rebalancing"]:
        state_emoji = "⏳"
        status_text = f"Sedang proses rebalance — {status['from_exchange']} → {status['to_exchange']} (${status['amount_to_transfer']:.2f})"
    elif status["is_balanced"]:
        state_emoji = "🟢"
        status_text = "Seimbang"
    else:
        state_emoji = "🔴"
        status_text = "Tidak Seimbang"

    lines = [
        "⚖️ *BALANCE STATUS*\n",
        f"├ Bybit:  `${status['bybit_balance']:.2f}` ({bb_pct}%)",
        f"├ KuCoin: `${status['kucoin_balance']:.2f}` ({kc_pct}%)",
        f"└ Total:  `${status['total']:.2f}`\n",
        f"Status: {state_emoji} {status_text}",
        f"Threshold: {status['threshold']*100:.0f}%/{100-status['threshold']*100:.0f}%",
        f"Mode: {mode_str}",
    ]

    if not status["is_balanced"] and not status["is_rebalancing"]:
        lines += [
            "",
            f"Transfer diperlukan: `${status['amount_to_transfer']:.2f}` dari *{status['from_exchange']}* → *{status['to_exchange']}*",
        ]

    lines += [
        "",
        "⚙️ *Subcommands:*",
        "`/rebalance on` — Aktifkan auto rebalance",
        "`/rebalance off` — Nonaktifkan auto rebalance",
        "`/rebalance transfers` — Lihat 5 transfer terakhir",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")