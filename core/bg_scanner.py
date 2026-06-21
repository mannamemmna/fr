"""Background scanner thread — auto-scans every AUTO_SCAN_INTERVAL seconds."""

from __future__ import annotations

import asyncio
import logging
import threading

from config import AUTO_SCAN_INTERVAL, BOT_TOKEN
from core.scanner import run_scan
from handlers.state import exchange_health, last_scan

log = logging.getLogger(__name__)

_bg_scanner_thread: threading.Thread | None = None
_bg_scanner_stop = threading.Event()


def _send_alert(application, message: str):
    """Thread-safe notification to registered chat."""
    from handlers.state import _notify_chat_id
    if not _notify_chat_id:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            application.bot.send_message(
                chat_id=_notify_chat_id, text=message, parse_mode="Markdown"
            ),
            application.loop,
        )
    except Exception:
        log.debug("Cannot send alert to %s", _notify_chat_id)


def start_bg_scanner(application):
    """Start the background scanner thread."""
    global _bg_scanner_thread
    if _bg_scanner_thread and _bg_scanner_thread.is_alive():
        return
    _bg_scanner_stop.clear()
    _bg_scanner_thread = threading.Thread(
        target=_bg_scanner_loop,
        args=(application,),
        daemon=True,
        name="bg-scanner",
    )
    _bg_scanner_thread.start()
    log.info("Auto-scan enabled: every %ds", AUTO_SCAN_INTERVAL)


def stop_bg_scanner():
    """Signal the background scanner to stop."""
    _bg_scanner_stop.set()


def _bg_scanner_loop(application):
    global last_scan, exchange_health
    log.info("Background scanner started (interval=%ds)", AUTO_SCAN_INTERVAL)
    while not _bg_scanner_stop.is_set():
        try:
            payload = run_scan()
            last_scan = payload
            n_opps = len(payload.get("opportunities", []))
            log.info("Auto-scan: %d opportunities in %.1fs", n_opps, payload.get("scan_duration", 0))

            for name in ("bybit", "kucoin"):
                count_key = f"{name}_count"
                if not exchange_health.get(name, True) and payload.get(count_key, 0) > 0:
                    exchange_health[name] = True
                    _send_alert(application, f"🟢 *{name.upper()}* is back online!")

        except Exception as e:
            err_msg = str(e)[:200]
            log.error("Auto-scan failed: %s", err_msg)

            for name in ("bybit", "kucoin"):
                if name.lower() in err_msg.lower() or "timeout" in err_msg.lower() or "connect" in err_msg.lower():
                    if exchange_health.get(name, True):
                        exchange_health[name] = False
                        _send_alert(application, f"🔴 *{name.upper()}* DOWN — `{err_msg}`")

            _send_alert(application, f"⚠️ Auto-scan failed:\\n`{err_msg}`")

        _bg_scanner_stop.wait(AUTO_SCAN_INTERVAL)
