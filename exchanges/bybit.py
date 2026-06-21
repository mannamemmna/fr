"""Bybit V5 client.

Sources:
    Bulk funding: https://api.bybit.com/v5/market/tickers?category=linear
        (returns fundingRate, nextFundingTime, markPrice, indexPrice,
         fundingIntervalHour for ~720 USDT perps in 1 call)

Trading: ccxt.bybit with options.defaultType='swap'
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from .base import BaseExchangeClient, FundingRate, Ticker
from .symbols import bybit_to_unified

log = logging.getLogger(__name__)

REST_URL = "https://api.bybit.com/v5/market/tickers?category=linear"


class BybitClient(BaseExchangeClient):
    name = "bybit"
    ccxt_id = "bybit"

    def fetch_all_funding_rates(self) -> dict[str, FundingRate]:
        log.info("Bybit: bulk fetch starting…")
        t0 = time.time()
        r = requests.get(REST_URL, timeout=20)
        d = r.json()
        if d.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {d.get('retMsg')}")

        out: dict[str, FundingRate] = {}
        for row in d.get("result", {}).get("list", []):
            fr = _safe_float(row.get("fundingRate"))
            if fr is None:
                continue
            nft = _safe_int(row.get("nextFundingTime"))
            mark = _safe_float(row.get("markPrice"))
            idx = _safe_float(row.get("indexPrice"))
            interval = _safe_int(row.get("fundingIntervalHour")) or 8

            unified = bybit_to_unified(row["symbol"])
            out[unified] = FundingRate(
                symbol=unified,
                raw_symbol=row["symbol"],
                funding_rate=fr,
                next_payment_rate=fr,           # Bybit: current rate IS next payment
                mark_price=mark,
                index_price=idx,
                funding_next_time=nft,
                interval_hours=interval,
            )
        log.info("Bybit: %d rates in %.2fs", len(out), time.time() - t0)
        return out

    def fetch_ticker(self, unified_symbol: str) -> Optional[Ticker]:
        from .symbols import unified_to_native
        raw = unified_to_native(self.name, unified_symbol)
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": raw},
                timeout=10,
            )
            d = r.json()
            row = (d.get("result", {}).get("list") or [None])[0]
            if not row:
                return None
            return Ticker(
                symbol=unified_symbol,
                last=_safe_float(row.get("lastPrice")) or 0,
                bid=_safe_float(row.get("bid1Price")),
                ask=_safe_float(row.get("ask1Price")),
                mark=_safe_float(row.get("markPrice")),
                index=_safe_float(row.get("indexPrice")),
                ts=_safe_int(row.get("nextFundingTime")) or 0,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Bybit fetch_ticker(%s) failed: %s", unified_symbol, e)
            return None


def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
