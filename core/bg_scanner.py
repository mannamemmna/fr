"""Background scanner thread — auto-scans every AUTO_SCAN_INTERVAL seconds.

Telegram notifications use raw HTTP API (requests.post).
All shared state accessed via handlers.state module (not imported by value).
"""

from __future__ import annotations

import logging
import threading

from config import AUTO_SCAN_INTERVAL, BOT_TOKEN
from core.scanner import run_scan
import handlers.state as state

log = logging.getLogger(__name__)

_bg_scanner_thread: threading.Thread | None = None
_bg_scanner_stop = threading.Event()


def _send_alert(message: str):
    """Thread-safe notification via raw Telegram API — no event loop needed."""
    chat_id = state._notify_chat_id
    if not chat_id:
        log.debug("_send_alert: no notify_chat_id set, skipping")
        return
    import requests as _r
    try:
        resp = _r.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=5,
        )
        if not resp.ok:
            log.warning("Alert HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Cannot send alert to %s: %s", chat_id, e)


def start_bg_scanner():
    """Start the background scanner thread."""
    global _bg_scanner_thread
    if _bg_scanner_thread and _bg_scanner_thread.is_alive():
        return
    _bg_scanner_stop.clear()
    _bg_scanner_thread = threading.Thread(
        target=_bg_scanner_loop,
        daemon=True,
        name="bg-scanner",
    )
    _bg_scanner_thread.start()
    log.info("Auto-scan enabled: every %ds", AUTO_SCAN_INTERVAL)


def stop_bg_scanner():
    _bg_scanner_stop.set()


def _bg_scanner_loop():
    log.info("Background scanner started (interval=%ds)", AUTO_SCAN_INTERVAL)
    while not _bg_scanner_stop.is_set():
        try:
            payload = run_scan()
            state.last_scan = payload          # ← akses via module, bukan import by value
            n_opps = len(payload.get("opportunities", []))
            log.info("Auto-scan: %d opportunities in %.1fs", n_opps, payload.get("scan_duration", 0))

            # Exchange recovery alert
            for name in ("bybit", "kucoin"):
                count_key = f"{name}_count"
                was_down = not state.exchange_health.get(name, True)
                now_up = payload.get(count_key, 0) > 0
                if was_down and now_up:
                    state.exchange_health[name] = True
                    _send_alert(f"🟢 {name.upper()} kembali online!")

        except Exception as e:
            err_msg = str(e)[:200]
            log.error("Auto-scan failed: %s", err_msg)

            for name in ("bybit", "kucoin"):
                if name.lower() in err_msg.lower() or "timeout" in err_msg.lower() or "connect" in err_msg.lower():
                    if state.exchange_health.get(name, True):
                        state.exchange_health[name] = False
                        _send_alert(f"🔴 {name.upper()} DOWN\n{err_msg}")

            _send_alert(f"⚠️ Auto-scan error\n{err_msg}")

        _bg_scanner_stop.wait(AUTO_SCAN_INTERVAL)
