"""Estimasi Funding PnL — dipakai bersama oleh PaperEngine dan LiveEngine
supaya kedua mode menghitung funding P&L dengan matematika yang identik
(single source of truth, lebih mudah diaudit & ditest).
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from typing import TypedDict


class FundingPnlResult(TypedDict):
    fr_paid: float
    fr_received: float
    funding_pnl: float
    hours_held: float


def compute_funding_pnl(
    *,
    entry_rate_bybit_pct: float,
    entry_rate_kucoin_pct: float,
    bybit_interval_h: int,
    kucoin_interval_h: int,
    position_size: float,
    side_bybit: str,
    side_kucoin: str,
    entry_time_iso: str,
    now_ts: float | None = None,
) -> FundingPnlResult:
    """Estimasi funding PnL yang terakumulasi sejak entry, berdasarkan
    funding rate SAAT ENTRY dan lama holding (estimasi linear — tidak
    memperhitungkan perubahan rate di siklus funding berikutnya selama
    holding period panjang).

    entry_rate_*_pct: funding rate dalam PERSEN (mis. 0.0123 berarti
        0.0123%), sesuai field `bybit_rate_pct`/`kucoin_rate_pct` yang
        dihasilkan core/scanner.py dan core/spread_engine.py.
    position_size: notional posisi dalam USD (amount_usd * leverage).
    """
    now = now_ts if now_ts is not None else _time.time()

    entry_rate_bb = entry_rate_bybit_pct / 100.0
    entry_rate_kc = entry_rate_kucoin_pct / 100.0
    bb_iv = max(int(bybit_interval_h or 8), 1)
    kc_iv = max(int(kucoin_interval_h or 8), 1)

    try:
        entry_dt = datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00"))
        hours_held = max(0.0, (now - entry_dt.timestamp()) / 3600.0)
    except (ValueError, AttributeError):
        hours_held = 0.0

    raw_bb = entry_rate_bb * position_size * (hours_held / bb_iv)
    raw_kc = entry_rate_kc * position_size * (hours_held / kc_iv)

    # FR positif: SHORT menerima, LONG membayar.
    # FR negatif: SHORT membayar, LONG menerima.
    if side_bybit == "sell":
        fr_received_bb, fr_paid_bb = max(raw_bb, 0.0), max(-raw_bb, 0.0)
    else:
        fr_paid_bb, fr_received_bb = max(raw_bb, 0.0), max(-raw_bb, 0.0)

    if side_kucoin == "sell":
        fr_received_kc, fr_paid_kc = max(raw_kc, 0.0), max(-raw_kc, 0.0)
    else:
        fr_paid_kc, fr_received_kc = max(raw_kc, 0.0), max(-raw_kc, 0.0)

    fr_paid = fr_paid_bb + fr_paid_kc
    fr_received = fr_received_bb + fr_received_kc

    return {
        "fr_paid": fr_paid,
        "fr_received": fr_received,
        "funding_pnl": fr_received - fr_paid,
        "hours_held": hours_held,
    }
