"""Delisting Protection — monitor pengumuman delisting Bybit & KuCoin,
blacklist simbol yang terdeteksi, cegah entry baru & alert posisi terbuka.

Sumber data (endpoint publik, tidak butuh API key):
  - Bybit:  GET https://api.bybit.com/v5/announcements/index
  - KuCoin: GET https://api.kucoin.com/api/v3/announcements?annType=delistings

Parsing simbol dari judul itu best-effort. Prinsip fail-safe: begitu simbol
terdeteksi, LANGSUNG diblokir dari entry baru — apapun confidence-nya.
Worst-case parsing salah = kehilangan 1 peluang trading (murah), lebih baik
daripada worst-case entry ke pair yang beneran delisting (mahal). Operator
bisa override cepat via /blacklist remove SYMBOL.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

import requests

from core.db import get_db
from core.scanner import read_opportunities

log = logging.getLogger("fr-bot.delisting")

BYBIT_ANNOUNCEMENT_URL = "https://api.bybit.com/v5/announcements/index"
KUCOIN_ANNOUNCEMENT_URL = "https://api.kucoin.com/api/v3/announcements"

# Kata umum yang bisa ke-capture regex tapi BUKAN ticker — exclude eksplisit.
_NOT_A_SYMBOL = {"UTC", "USD", "USDT", "AM", "PM", "EST", "GMT", "THE", "AND", "FOR", "NEW"}

# "... delisting the XXXUSDT Perpetual Contract ..." / "... Will Delist the XXXUSDT Perpetual Contract ..."
_PERP_DELIST_RE = re.compile(
    r"\b([A-Z][A-Z0-9]{1,14})USDT\b[^.]{0,60}?(?:Perpetual|Futures)\s+Contract",
    re.IGNORECASE,
)
# "LSS, PBUX, CLAY, ... will be delisted at 08:00:00 on November 19, 2025 (UTC)"
_MULTI_SYMBOL_RE = re.compile(
    r"([A-Z0-9]{2,15}(?:\s*,\s*[A-Z0-9]{2,15}){1,})\s*(?:,?\s*and\s+[A-Z0-9]{2,15})?\s+will\s+be\s+delisted",
    re.IGNORECASE,
)

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


# ─── Fetch pengumuman ───────────────────────────────────────────────────

def _fetch_bybit_announcements(pages: int = 3, page_size: int = 20) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        try:
            r = requests.get(BYBIT_ANNOUNCEMENT_URL, params={
                "locale": "en-US", "page": page, "limit": page_size,
            }, timeout=15)
            if not r.ok:
                log.warning("Bybit announcement fetch HTTP %s", r.status_code)
                break
            items = r.json().get("result", {}).get("list", [])
            if not items:
                break
            out.extend(items)
        except Exception:
            log.exception("Bybit announcement fetch failed (page %d)", page)
            break
    return out


def _fetch_kucoin_announcements(pages: int = 3, page_size: int = 50) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        try:
            r = requests.get(KUCOIN_ANNOUNCEMENT_URL, params={
                "currentPage": page, "pageSize": page_size,
                "annType": "delistings", "lang": "en_US",
            }, timeout=15)
            if not r.ok:
                log.warning("KuCoin announcement fetch HTTP %s", r.status_code)
                break
            items = r.json().get("data", {}).get("items", [])
            if not items:
                break
            out.extend(items)
        except Exception:
            log.exception("KuCoin announcement fetch failed (page %d)", page)
            break
    return out


# ─── Parsing ─────────────────────────────────────────────────────────────

def _extract_perp_symbol(title: str) -> Optional[str]:
    """Parse simbol dari judul delisting kontrak perpetual (HIGH confidence
    kalau simbolnya dikenali)."""
    if "delist" not in title.lower():
        return None
    m = _PERP_DELIST_RE.search(title)
    if not m:
        return None
    sym = m.group(1).upper()
    if sym in _NOT_A_SYMBOL:
        return None
    return sym


def _extract_multi_symbols(title: str, description: str = "") -> list[str]:
    """Parse banyak simbol dari pengumuman delisting massal gaya
    'Special Treatment' KuCoin — fallback, dipakai kalau _extract_perp_symbol
    tidak match."""
    text = f"{title} {description}"
    m = _MULTI_SYMBOL_RE.search(text)
    if not m:
        return []
    candidates = [s.strip().upper() for s in m.group(1).split(",")]
    return [c for c in candidates if c and c not in _NOT_A_SYMBOL and c.isalnum()]


def _known_symbols() -> set[str]:
    """Universe simbol yang saat ini di-track bot (dari scan terakhir) —
    dipakai untuk menandai confidence, BUKAN untuk memutuskan blokir/tidak."""
    data = read_opportunities()
    return {o["symbol"].upper() for o in data.get("opportunities", [])}


# ─── Proses satu batch pengumuman per exchange ────────────────────────────

def _process_bybit(db, notify_cb) -> None:
    checkpoint = db.get_delisting_checkpoint("bybit")
    items = _fetch_bybit_announcements()
    known = _known_symbols()
    newest_ts = checkpoint

    for item in items:
        ts_sec = (item.get("dateTimestamp", 0) or 0) // 1000
        if ts_sec <= checkpoint:
            continue
        newest_ts = max(newest_ts, ts_sec)

        title = item.get("title", "")
        url = item.get("url", "")
        sym = _extract_perp_symbol(title)
        if not sym:
            continue

        confidence = "high" if sym in known else "manual"
        is_new = db.add_to_blacklist(
            symbol=sym, exchange="bybit", confidence=confidence,
            reason=title, delist_ts=None, announcement_url=url, source_title=title,
        )
        if is_new:
            log.warning("DELISTING terdeteksi (Bybit, confidence=%s): %s — %s", confidence, sym, title)
            notify_cb(sym, "bybit", confidence, title, url)

    if newest_ts > checkpoint:
        db.set_delisting_checkpoint("bybit", newest_ts)


def _process_kucoin(db, notify_cb) -> None:
    checkpoint = db.get_delisting_checkpoint("kucoin")
    items = _fetch_kucoin_announcements()
    known = _known_symbols()
    newest_ts = checkpoint

    for item in items:
        ts_sec = (item.get("cTime", 0) or 0) // 1000
        if ts_sec <= checkpoint:
            continue
        newest_ts = max(newest_ts, ts_sec)

        title = item.get("annTitle", "")
        desc = item.get("annDesc", "")
        url = item.get("annUrl", "")

        sym = _extract_perp_symbol(title)
        if sym:
            confidence = "high" if sym in known else "manual"
            is_new = db.add_to_blacklist(
                symbol=sym, exchange="kucoin", confidence=confidence,
                reason=title, delist_ts=None, announcement_url=url, source_title=title,
            )
            if is_new:
                log.warning("DELISTING terdeteksi (KuCoin, confidence=%s): %s — %s", confidence, sym, title)
                notify_cb(sym, "kucoin", confidence, title, url)
            continue

        # Fallback: delisting massal (spot/ST). Fail-safe: blacklist SEMUA
        # simbol yang berhasil di-parse, apapun statusnya di `known` — cuma
        # confidence yang berbeda. Simbol yang lagi tidak ada di scan tetap
        # diblokir (manual confidence) supaya kalau dia muncul lagi di scan
        # berikutnya, dia sudah terblokir duluan.
        for sym in _extract_multi_symbols(title, desc):
            confidence = "high" if sym in known else "manual"
            is_new = db.add_to_blacklist(
                symbol=sym, exchange="kucoin", confidence=confidence,
                reason=title, delist_ts=None, announcement_url=url, source_title=title,
            )
            if is_new:
                log.warning("DELISTING massal terdeteksi (KuCoin, confidence=%s): %s — %s",
                            confidence, sym, title)
                notify_cb(sym, "kucoin", confidence, title, url)

    if newest_ts > checkpoint:
        db.set_delisting_checkpoint("kucoin", newest_ts)


# ─── Public API ────────────────────────────────────────────────────────

def get_blacklisted_symbols() -> set[str]:
    """Baca dari DB — murah, tidak ada network call, aman dipanggil sesering
    apapun (dipakai automation_engine tiap tick)."""
    return get_db().get_blacklisted_symbols()


def is_blacklisted(symbol: str) -> bool:
    return get_db().is_blacklisted(symbol)


def check_now(notify_cb=None) -> dict:
    """Satu siklus cek pengumuman delisting untuk kedua exchange. Dipanggil
    background thread tiap DELISTING_CHECK_INTERVAL_SEC, atau manual lewat
    /blacklist check."""
    db = get_db()
    notify_cb = notify_cb or (lambda *a: None)
    before = db.get_blacklisted_symbols()
    _process_bybit(db, notify_cb)
    _process_kucoin(db, notify_cb)
    after = db.get_blacklisted_symbols()
    return {"new_entries": sorted(after - before), "total_blacklisted": len(after)}


# ─── Background thread ───────────────────────────────────────────────────

def start_delisting_monitor(notify_cb=None):
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, args=(notify_cb,), daemon=True, name="delisting-monitor")
    _thread.start()
    log.info("Delisting monitor started")


def stop_delisting_monitor():
    _stop_event.set()


def _loop(notify_cb):
    from config import DELISTING_CHECK_INTERVAL_SEC

    # Skip histori lama di run pertama — set checkpoint ke "sekarang" kalau
    # belum pernah dicek sama sekali, supaya tidak banjir alert untuk
    # pengumuman delisting yang sudah lama lewat.
    db = get_db()
    now_ts = int(time.time())
    for ex in ("bybit", "kucoin"):
        if db.get_delisting_checkpoint(ex) == 0:
            db.set_delisting_checkpoint(ex, now_ts)

    log.info("Delisting monitor loop started (interval=%ds)", DELISTING_CHECK_INTERVAL_SEC)
    while not _stop_event.is_set():
        try:
            result = check_now(notify_cb)
            if result["new_entries"]:
                log.warning("Delisting monitor: %d simbol baru diblacklist: %s",
                            len(result["new_entries"]), result["new_entries"])
        except Exception:
            log.exception("Delisting monitor cycle failed")
        _stop_event.wait(DELISTING_CHECK_INTERVAL_SEC)
