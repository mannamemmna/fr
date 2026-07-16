"""/rebalance — Cek dan trigger manual rebalance saldo Bybit ↔ KuCoin."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from core.tg_format import b, i, code, esc
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
            lines = [b("📋 TRANSFER TERAKHIR (5)")]
            for t in transfers:
                icon = "🟢" if t.get("type") == "internal_transfer_complete" else ("🔴" if "fail" in t.get("type", "") else "🟡")
                cid = t.get('client_id', '')[:12]
                fr = esc(t.get('from', '?'))
                to = esc(t.get('to', '?'))
                amt = t.get('amount', 0)
                typ = esc(t.get('type', '?'))
                lines.append(f"{icon} {code(cid)} | {fr}→{to} | ${amt:.2f} | {typ}")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

    status = rb.get_status()
    bb_pct = status["ratio_bybit"]
    kc_pct = status["ratio_kucoin"]

    mode_str = "📄 Paper (simulasi)" if PAPER_MODE else "🔴 Live"

    if status["is_rebalancing"]:
        state_emoji = "⏳"
        fr_ex = esc(status['from_exchange'])
        to_ex = esc(status['to_exchange'])
        status_text = f"Sedang proses rebalance — {fr_ex} → {to_ex} (${status['amount_to_transfer']:.2f})"
    elif status["is_balanced"]:
        state_emoji = "🟢"
        status_text = "Seimbang"
    else:
        state_emoji = "🔴"
        status_text = "Tidak Seimbang"

    bb_s = f"${status['bybit_balance']:.2f}"
    kc_s = f"${status['kucoin_balance']:.2f}"
    tot_s = f"${status['total']:.2f}"

    lines = [
        f"⚖️ {b('BALANCE STATUS')}\n",
        f"├ Bybit:  {code(bb_s)} ({bb_pct}%)",
        f"├ KuCoin: {code(kc_s)} ({kc_pct}%)",
        f"└ Total:  {code(tot_s)}\n",
        f"Status: {state_emoji} {status_text}",
        f"Threshold: {status['threshold']*100:.0f}%/{100-status['threshold']*100:.0f}%",
        f"Mode: {mode_str}",
    ]

    if not status["is_balanced"] and not status["is_rebalancing"]:
        xfer_s = f"${status['amount_to_transfer']:.2f}"
        lines += [
            "",
            f"Transfer diperlukan: {code(xfer_s)} dari {b(status['from_exchange'])} → {b(status['to_exchange'])}",
        ]

    lines += [
        "",
        f"⚙️ {b('Subcommands:')}",
        "<code>/rebalance on</code> — Aktifkan auto rebalance",
        "<code>/rebalance off</code> — Nonaktifkan auto rebalance",
        "<code>/rebalance transfers</code> — Lihat 5 transfer terakhir",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
