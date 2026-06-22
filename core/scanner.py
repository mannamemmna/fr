"""Funding rate scanner — Bybit × KuCoin arbitrage opportunities.

Pulls all USDT-margined perp funding rates from both venues in parallel,
matches common symbols, and builds an opportunity list sorted by delta (|\u0394|).

Public API:
    run_scan()                  → dict (also written to data/opportunities.json)
    find_opportunities(...)     → list of opportunity rows
    read_opportunities()        → load latest scan from disk
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from exchanges import get_client, list_supported
from config import DATA_DIR

OPPORTUNITIES_FILE = os.path.join(DATA_DIR, "opportunities.json")


# ─── Opportunity builder ──────────────────────────────────────────────────

def find_opportunities(bybit_rates: dict, kucoin_rates: dict) -> list[dict]:
    """For every unified symbol present on both exchanges, build a row."""
    common = sorted(set(bybit_rates) & set(kucoin_rates))
    opps: list[dict] = []

    for sym in common:
        b = bybit_rates[sym]
        k = kucoin_rates[sym]

        bb_r, kc_r = b.funding_rate, k.funding_rate
        raw_fr_diff = bb_r - kc_r

        bb_per_day = 24 / max(b.interval_hours, 1)
        kc_per_day = 24 / max(k.interval_hours, 1)
        per_day = (bb_per_day + kc_per_day) / 2

        net_daily = abs(raw_fr_diff) * per_day
        annual = net_daily * 365

        if raw_fr_diff > 0:
            direction = "SHORT Bybit / LONG KuCoin"
            bybit_action, kucoin_action = "SHORT", "LONG"
        elif raw_fr_diff < 0:
            direction = "SHORT KuCoin / LONG Bybit"
            bybit_action, kucoin_action = "LONG", "SHORT"
        else:
            direction = "FLAT"
            bybit_action, kucoin_action = "—", "—"

        price = k.mark_price or b.mark_price or k.index_price or b.index_price

        bb_mark = b.mark_price or b.index_price or 0
        kc_mark = k.mark_price or k.index_price or 0
        price_spread = 0.0
        if bb_mark > 0 and kc_mark > 0:
            p_short = bb_mark if bybit_action == "SHORT" else kc_mark
            p_long = kc_mark if kucoin_action == "LONG" else bb_mark
            if p_short > 0:
                price_spread = ((p_long - p_short) / p_short) * 100.0

        bb_next_ts = (b.funding_next_time // 1000) if b.funding_next_time else None
        kc_next_ts = (k.funding_next_time // 1000) if k.funding_next_time else None

        def _fmt_wib(ts_sec):
            if not ts_sec:
                return ""
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            wib = dt.astimezone(timezone(timedelta(hours=7)))
            return wib.strftime("%H:%M WIB")

        next_ts = k.funding_next_time or b.funding_next_time
        next_iso = None
        next_short = ""
        if next_ts:
            dt = datetime.fromtimestamp(next_ts / 1000, tz=timezone.utc)
            next_iso = dt.isoformat()
            next_short = dt.strftime("%H:%M UTC")

        np_spread = b.next_payment_rate - k.next_payment_rate
        base = sym.split("/")[0]

        # Funding Difference (normalized by max interval)
        max_interval = max(b.interval_hours, k.interval_hours, 1)
        # Normalize FR to max_interval so they are comparable
        # e.g., BB 1h (0.01%), KC 4h (0.03%)
        # -> BB norm = 0.01% * (4/1) = 0.04%
        # -> KC norm = 0.03% * (4/4) = 0.03%
        # -> Raw Diff = 0.04% - 0.03% = +0.01% (per 4h)
        bb_norm = bb_r * (max_interval / max(b.interval_hours, 1))
        kc_norm = kc_r * (max_interval / max(k.interval_hours, 1))
        
        raw_diff = bb_norm - kc_norm
        funding_diff_pct = round(abs(raw_diff) * 100, 6)
        # Net daily is simply the raw diff extrapolated to 24h
        diff_daily_pct = round(abs(raw_diff) * (24 / max_interval) * 100, 4)

        opps.append({
            "symbol": base,
            "unified_symbol": sym,
            "spread_pct": round(price_spread, 6),
            "spread_abs": round(abs(price_spread), 6),
            "raw_fr_diff": round(raw_fr_diff * 100, 6),
            "funding_diff_pct": funding_diff_pct,
            "delta_pct": funding_diff_pct,  # keep for backwards compatibility if needed, but we'll use funding_diff_pct
            "diff_daily_pct": diff_daily_pct,
            "next_payment_spread_pct": round(np_spread * 100, 6),
            "bybit_rate_pct": round(bb_r * 100, 6),
            "kucoin_rate_pct": round(kc_r * 100, 6),
            "bybit_next_payment_pct": round(b.next_payment_rate * 100, 6),
            "kucoin_next_payment_pct": round(k.next_payment_rate * 100, 6),
            "direction": direction,
            "bybit_action": bybit_action,
            "kucoin_action": kucoin_action,
            "net_daily_pct": round(net_daily * 100, 4),
            "annual_pct": round(annual * 100, 2),
            "price": price,
            "next_funding": next_short,
            "next_funding_iso": next_iso,
            "next_funding_ts": next_ts // 1000 if next_ts else None,
            "bybit_interval_h": b.interval_hours,
            "kucoin_interval_h": k.interval_hours,
            "bybit_next_ts": bb_next_ts,
            "kucoin_next_ts": kc_next_ts,
            "bybit_next_time": _fmt_wib(bb_next_ts),
            "kucoin_next_time": _fmt_wib(kc_next_ts),
            "bybit_raw": b.raw_symbol,
            "kucoin_raw": k.raw_symbol,
            "bybit_mark": bb_mark,
            "kucoin_mark": kc_mark,
        })

    opps.sort(key=lambda o: o.get("funding_diff_pct", 0), reverse=True)
    return opps


# ─── Main scanner ─────────────────────────────────────────────────────────

def run_scan() -> dict:
    """Fetch both venues in parallel, find opportunities, persist to disk."""
    start = time.time()

    bybit = get_client("bybit")
    kucoin = get_client("kucoin")

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_bb = ex.submit(bybit.fetch_all_funding_rates)
        f_kc = ex.submit(kucoin.fetch_all_funding_rates)
        bb_rates = f_bb.result()
        kc_rates = f_kc.result()

    fetch_secs = time.time() - start
    opps = find_opportunities(bb_rates, kc_rates)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fetch_duration": round(fetch_secs, 2),
        "scan_duration": round(time.time() - start, 3),
        "bybit_count": len(bb_rates),
        "kucoin_count": len(kc_rates),
        "common_count": len(set(bb_rates) & set(kc_rates)),
        "opportunities": opps,
    }

    with open(OPPORTUNITIES_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    return payload


def read_opportunities() -> dict:
    """Load the latest scan from disk. Returns empty payload if missing."""
    if not os.path.exists(OPPORTUNITIES_FILE):
        return {
            "timestamp": None,
            "bybit_count": 0,
            "kucoin_count": 0,
            "common_count": 0,
            "opportunities": [],
        }
    try:
        with open(OPPORTUNITIES_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {
            "timestamp": None,
            "bybit_count": 0,
            "kucoin_count": 0,
            "common_count": 0,
            "opportunities": [],
        }
