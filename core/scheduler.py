"""Built-in scheduled notifications — runs inside the bot process via PTB JobQueue.

Jobs:
  - daily_summary  : every day at 00:00 UTC
  - hourly_check   : every hour — cek health exchange + alert kalau ada masalah
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, time as dtime

from telegram.ext import Application

from config import BOT_TOKEN, NOTIFY_CHAT_ID, PAPER_MODE
import handlers.state as state

log = logging.getLogger("scheduler")


def _send(text: str):
    """Fire-and-forget Telegram message (sync, from job thread)."""
    if not NOTIFY_CHAT_ID:
        return
    import requests as _r
    try:
        resp = _r.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": NOTIFY_CHAT_ID, "text": text},
            timeout=8,
        )
        if not resp.ok:
            log.warning("Scheduler send HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Scheduler send failed: %s", e)


async def _job_daily_summary(context):
    """Send daily PnL summary."""
    if not state.paper_engine:
        return

    try:
        summary = state.paper_engine.get_summary()
        closed = state.paper_engine.get_closed_positions()

        now = datetime.now(timezone.utc)
        pnl_1d = 0.0
        for p in closed:
            ts = p.get("exit_time") or p.get("closed_at", "")
            if not ts:
                continue
            try:
                closed_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (now - closed_at) <= timedelta(days=1):
                    pnl_1d += p.get("realized_pnl", 0)
            except Exception:
                pass

        mode = "Paper" if PAPER_MODE else "Live"
        pnl_emoji = "🟢" if summary["total_pnl"] >= 0 else "🔴"
        day_emoji = "🟢" if pnl_1d >= 0 else "🔴"

        msg = (
            f"📊 RINGKASAN HARIAN — {now.strftime('%d %b %Y')}\n"
            f"Mode: {mode}\n\n"
            f"{day_emoji} PnL 24 jam: {pnl_1d:+.2f} USD\n"
            f"{pnl_emoji} Total PnL: {summary['total_pnl']:+.2f} USD\n"
            f"   Direalisasi: {summary['realized_pnl']:+.2f}\n"
            f"   Belum: {summary['unrealized_pnl']:+.2f}\n\n"
            f"Saldo: ${summary['balance']:.2f}\n"
            f"Posisi terbuka: {summary['open_positions']}\n"
            f"Total biaya: {summary['total_fees']:.2f} USD"
        )
        _send(msg)
        log.info("Daily summary sent")
    except Exception as e:
        log.error("Daily summary failed: %s", e)


async def _job_hourly_check(context):
    """Check exchange health and alert if down."""
    alerts = []
    for name in ("bybit", "kucoin"):
        if not state.exchange_health.get(name, True):
            alerts.append(f"🔴 {name.upper()} masih DOWN")
    if alerts:
        _send("⚠️ Exchange Health Alert\n" + "\n".join(alerts))


def register_jobs(app: Application):
    """Register all scheduled jobs into PTB JobQueue."""
    if not NOTIFY_CHAT_ID:
        log.warning("NOTIFY_CHAT_ID not set — scheduled notifications disabled")
        return

    jq = app.job_queue
    if jq is None:
        log.warning("JobQueue not available — install python-telegram-bot[job-queue]")
        return

    # Daily summary jam 00:00 UTC
    jq.run_daily(
        _job_daily_summary,
        time=dtime(hour=0, minute=0, tzinfo=timezone.utc),
        name="daily_summary",
    )

    # Hourly exchange health check
    jq.run_repeating(
        _job_hourly_check,
        interval=3600,
        first=300,  # mulai 5 menit setelah bot start
        name="hourly_health",
    )

    log.info("Scheduled jobs registered: daily_summary + hourly_health")
    _send(
        f"🤖 FR Bot online!\n"
        f"Mode: {'Paper (Simulasi)' if PAPER_MODE else 'Live (Real)'}\n"
        f"Notifikasi aktif. Daily summary jam 00:00 UTC.\n"
        f"Ketik /help untuk daftar perintah."
    )
