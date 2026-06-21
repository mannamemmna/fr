"""/status — Real-time status dashboard."""

from __future__ import annotations
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE, AUTO_SCAN_INTERVAL, AUTO_MONITOR_INTERVAL, AUTO_ENTRY_WINDOW_MIN
from core.scanner import read_opportunities
import handlers.state as state


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = time.time()

    mode = "📄 Paper (Simulasi)" if PAPER_MODE else "🔴 Live (Real)"

    # ── Balance ──
    if state.paper_engine:
        summary = state.paper_engine.get_summary()
        balance = f"${summary.get('balance', 0):.2f}"
        total_pnl_val = summary.get("total_pnl", 0)
        total_pnl = f"{total_pnl_val:+.2f}"
        realized_val = summary.get("realized_pnl", 0)
        realized = f"{realized_val:+.2f}"
        open_positions = summary.get("positions", [])
    else:
        balance = "—"
        total_pnl = "—"
        realized = "—"
        open_positions = []

    # ── Exchange health ──
    bb_icon = "🟢" if state.exchange_health.get("bybit", True) else "🔴"
    kc_icon = "🟢" if state.exchange_health.get("kucoin", True) else "🔴"

    # ── Auto engine state ──
    if state.auto_engine:
        eng = state.auto_engine.get_status()
        eng_enabled = eng.get("enabled", False)
        eng_state = eng.get("state_desc", "—")
        if eng.get("delay"):
            d = eng["delay"]
            eng_detail = (
                f"🔸 Pair: *{d['symbol']}*  |  {d['side_bb'].upper()}/{d['side_kc'].upper()}\n"
                f"🔸 Delta: `{d['delta']:.4f}%`  |  Stabil: {d['stable']} checks\n"
                f"🔸 Modal: ${d['amount']:.0f} × {d['leverage']}x"
            )
        elif eng.get("live_position"):
            eng_detail = f"🔸 Posisi: `{eng['live_position'][:8]}...`"
        else:
            eng_detail = f"🔸 State: {eng_state}"
    else:
        eng_enabled = False
        eng_detail = "🔸 Belum diinisialisasi"

    # ── Next funding ──
    next_funding = "—"
    top_sym = "—"
    if not state.last_scan:
        state.last_scan = read_opportunities()
    opps = state.last_scan.get("opportunities", [])
    if opps:
        best = opps[0]
        top_sym = best.get("symbol", "—")
        bb_ts = best.get("bybit_next_ts", 0) or 0
        kc_ts = best.get("kucoin_next_ts", 0) or 0
        valid_ts = [t for t in (bb_ts, kc_ts) if t > 0]
        if valid_ts:
            next_ts = min(valid_ts)
            if next_ts > now:
                mins = int((next_ts - now) / 60)
                next_funding = f"~{mins} menit"

    lines = [
        "*🤖 FR Bot Status*",
        "",
        f"🔹 Mode: `{mode}`",
        f"🔹 Saldo: `{balance}`",
        f"🔹 Total PnL: `{total_pnl}`",
        f"🔹 Direalisasi: `{realized}`",
        "",
        "*🔗 Koneksi Exchange*",
        f"🔹 {bb_icon} Bybit — {kc_icon} KuCoin",
        f"🔹 Scan setiap: {AUTO_SCAN_INTERVAL}s",
        "",
        "*⚙️ Auto Engine*",
        f"🔹 Status: {'✅ Aktif' if eng_enabled else '❌ Nonaktif'}",
        eng_detail,
        f"🔹 Window entry: {AUTO_ENTRY_WINDOW_MIN} menit sebelum funding",
        "",
    ]

    if open_positions:
        lines.append(f"*📊 Posisi Terbuka ({len(open_positions)})*")
        for p in open_positions:
            pid = p["id"][:8]
            sym = p["symbol"]
            side_bb = p.get("side_bybit", "?").upper()
            side_kc = p.get("side_kucoin", "?").upper()
            margin = p.get("amount_usd", 0)
            lev = p.get("leverage", "?")
            size = margin * lev if isinstance(lev, int) else margin
            lines.append(f"  `{pid}` {sym} — ${margin:.0f}×{lev}x=${size:.0f} | {side_bb}/{side_kc}")

    if next_funding != "—":
        lines += ["", f"*⏰ Funding Berikutnya*", f"  {next_funding} — pair teratas: *{top_sym}*"]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
