"""KuCoin Futures client.

Sources:
    Bulk funding: https://api-futures.kucoin.com/api/v1/contracts/active
        (returns fundingFeeRate, predictedFundingFeeRate,
         nextFundingRateDateTime, markPrice, indexPrice,
         fundingRateGranularity for ~648 USDT-margined contracts in 1 call)

Trading: ccxt.kucoinfutures with options.defaultType='swap'

Symbol notes:
    BTC is "XBTUSDTM" (XBT not BTC) on KuCoin Futures.
    "predictedFundingFeeRate" is usually null; UI shows fundingFeeRate.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from .base import BaseExchangeClient, FundingRate, Ticker
from .symbols import kucoin_to_unified, unified_to_native

log = logging.getLogger(__name__)

REST_URL = "https://api-futures.kucoin.com/api/v1/contracts/active"


class KuCoinClient(BaseExchangeClient):
    name = "kucoin"
    ccxt_id = "kucoinfutures"

    def fetch_all_funding_rates(self) -> dict[str, FundingRate]:
        log.info("KuCoin: bulk fetch starting…")
        t0 = time.time()
        r = requests.get(REST_URL, timeout=20)
        d = r.json()
        if d.get("code") != "200000":
            raise RuntimeError(f"KuCoin error: {d.get('msg')}")

        out: dict[str, FundingRate] = {}
        for row in d.get("data", []):
            sym = row.get("symbol", "")
            if not sym:
                continue
            # Filter to USDT-margined linear perps only
            if row.get("settleCurrency") != "USDT":
                continue
            if row.get("isInverse"):
                continue
            if row.get("status") and row["status"] != "Open":
                continue

            fr = _safe_float(row.get("fundingFeeRate"))
            if fr is None:
                continue

            predicted = row.get("predictedFundingFeeRate")
            # KuCoin UI displays fundingFeeRate as the "next payment rate"
            next_payment = _safe_float(predicted)
            if next_payment is None:
                next_payment = fr

            nft = _safe_int(row.get("nextFundingRateDateTime"))
            mark = _safe_float(row.get("markPrice"))
            idx = _safe_float(row.get("indexPrice"))
            granularity_ms = _safe_int(row.get("fundingRateGranularity")) or 28_800_000
            interval_h = granularity_ms // 3_600_000

            unified = kucoin_to_unified(sym)
            out[unified] = FundingRate(
                symbol=unified,
                raw_symbol=sym,
                funding_rate=fr,
                next_payment_rate=next_payment,
                mark_price=mark,
                index_price=idx,
                funding_next_time=nft,
                interval_hours=interval_h,
            )
        log.info("KuCoin: %d rates in %.2fs", len(out), time.time() - t0)
        return out

    def fetch_ticker(self, unified_symbol: str) -> Optional[Ticker]:
        raw = unified_to_native(self.name, unified_symbol)
        try:
            r = requests.get(
                "https://api-futures.kucoin.com/api/v1/ticker",
                params={"symbol": raw},
                timeout=10,
            )
            d = r.json()
            row = d.get("data")
            if not row or not isinstance(row, dict):
                return None
            return Ticker(
                symbol=unified_symbol,
                last=_safe_float(row.get("price")) or 0,
                bid=_safe_float(row.get("bestBidPrice")),
                ask=_safe_float(row.get("bestAskPrice")),
                mark=None,
                index=None,
                ts=_safe_int(row.get("ts")) or 0,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("KuCoin fetch_ticker(%s) failed: %s", unified_symbol, e)
            return None


def _safe_float(v) -> Optional[float]:
    if v is None or v == "" or v == 0:
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
