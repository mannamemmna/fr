"""WebSocket Connection Pool — Bybit + KuCoin real-time market data.

Architecture:
    ws_pool  ----callback---->  price_cache + funding_cache  ---->  spread_engine

Each exchange gets its own connection. Auto-reconnect with exponential backoff.
All subscriptions are re-sent on reconnect.

Public price/funding topics (this file) feed price_cache/funding_cache,
which drive candidate scoring and the entry/exit spread math. Private
account topics (position/order/balance) are a separate module --
see core/ws_private.py.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Callable, Optional

import requests
import websocket

from config_ws import (
    WS_RECONNECT_BASE,
    WS_RECONNECT_JITTER,
    WS_RECONNECT_MAX,
    WS_MIN_STABLE_SEC,
)
from config import WS_HEARTBEAT_SEC
from exchanges.bybit import BybitClient
from exchanges.kucoin import KuCoinClient
from core.market_cache import PriceCache, FundingCache
from core.rate_limiter import get_limiter

log = logging.getLogger("fr-bot.ws")

MessageCallback = Callable[[str, str, dict], None]


class WSConnection:
    """Single WebSocket connection to one exchange with auto-reconnect."""

    def __init__(self, name: str, url: str,
                 on_message: Callable,
                 on_connect: Optional[Callable] = None):
        self.name = name
        self.url = url
        self._on_message = on_message
        self._on_connect = on_connect
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._reconnect_count = 0
        self._connected_since: float = 0.0
        self._subscribed_topics: list[str] = []

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"ws-{self.name}")
        self._thread.start()
        log.info("[%s] WS connection started", self.name)

    def stop(self):
        self._stop.set()
        self._connected.clear()
        if self._ws:
            self._ws.close()
        log.info("[%s] WS connection stopped", self.name)

    def subscribe(self, topics: list[str]):
        self._subscribed_topics = topics

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._connect_and_listen()
            except Exception as e:
                log.error("[%s] WS error: %s", self.name, e)
            if self._stop.is_set():
                break
            if self._connected_since and (time.time() - self._connected_since) >= WS_MIN_STABLE_SEC:
                self._reconnect_count = 0
            self._connected_since = 0.0
            self._backoff()
        log.info("[%s] WS run loop ended", self.name)

    def _connect_and_listen(self):
        self._refresh_url()
        self._ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_ws_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(
            ping_interval=WS_HEARTBEAT_SEC,
            ping_timeout=5,
            skip_utf8_validation=False,
        )

    def _refresh_url(self):
        pass

    def _on_open(self, ws):
        self._connected.set()
        self._connected_since = time.time()
        log.info("[%s] WS connected", self.name)
        if self._subscribed_topics:
            self._subscribe_topics(ws)
        if self._on_connect:
            self._on_connect()

    def _on_ws_message(self, ws, message: str):
        if self._on_message:
            try:
                self._on_message(self.name, message)
            except Exception:
                log.exception("[%s] WS message handler error", self.name)

    def _on_error(self, ws, error):
        log.warning("[%s] WS error: %s", self.name, error)

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected.clear()
        log.info("[%s] WS closed (code=%s msg=%s)", self.name, close_status_code, close_msg)

    def _backoff(self):
        delay = min(
            WS_RECONNECT_MAX,
            WS_RECONNECT_BASE * (2 ** self._reconnect_count),
        )
        jitter = random.uniform(0, WS_RECONNECT_JITTER)
        total = delay + jitter
        self._reconnect_count += 1
        log.info("[%s] Reconnecting in %.1fs (attempt %d)", self.name, total, self._reconnect_count)
        self._stop.wait(total)

    def _subscribe_topics(self, ws):
        pass


class BybitWS(WSConnection):
    """Bybit V5 public linear WebSocket.

    Single topic (tickers.{symbol}) carries markPrice, fundingRate, AND
    bid1Price/ask1Price together in the same push -- confirmed against
    Bybit's V5 "Get Tickers" / WS tickers schema. No second subscription
    needed to get bid/ask; it was already flowing through, just not read.
    """

    TICKER_BASE = "tickers."

    def __init__(self, symbols: list[str],
                 price_cache: PriceCache,
                 funding_cache: FundingCache,
                 on_spread_update: Optional[MessageCallback] = None):
        url = "wss://stream.bybit.com/v5/public/linear"
        self._symbols = symbols
        self._price_cache = price_cache
        self._funding_cache = funding_cache
        self._on_spread_update = on_spread_update
        super().__init__("bybit", url, on_message=self._handle_message)

    def update_symbols(self, symbols: list[str]):
        self._symbols = symbols
        if self.is_connected():
            self._subscribe_topics(self._ws)

    def _subscribe_topics(self, ws):
        args = [f"{self.TICKER_BASE}{s}USDT" for s in self._symbols]
        sub = json.dumps({"op": "subscribe", "args": args})
        ws.send(sub)
        log.info("[bybit] Subscribed to %d tickers", len(self._symbols))

    def _handle_message(self, exchange: str, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("topic", "").startswith(self.TICKER_BASE):
            data = msg.get("data", {})
            if not data:
                return
            unified = self._topic_to_unified(msg["topic"])
            if not unified:
                return

            mark = _safe_float(data.get("markPrice"))
            bid = _safe_float(data.get("bid1Price"))
            ask = _safe_float(data.get("ask1Price"))
            if mark is not None or bid is not None or ask is not None:
                self._price_cache.update("bybit", unified, mark=mark, bid=bid, ask=ask)

            fr = _safe_float(data.get("fundingRate"))
            if fr is not None:
                nft = _safe_int(data.get("nextFundingTime"))
                interval = _safe_int(data.get("fundingIntervalHour")) or 8
                self._funding_cache.update("bybit", unified, fr, fr, nft, interval)
                self._on_spread_update and self._on_spread_update("bybit", "funding", {
                    "symbol": unified, "funding_rate": fr,
                    "next_funding_ts": nft, "interval_h": interval,
                })
            elif bid is not None or ask is not None:
                self._on_spread_update and self._on_spread_update("bybit", "price", {"symbol": unified})

    def _topic_to_unified(self, topic: str) -> Optional[str]:
        prefix = self.TICKER_BASE
        if not topic.startswith(prefix):
            return None
        raw = topic[len(prefix):]
        from exchanges.symbols import bybit_to_unified
        return bybit_to_unified(raw)


class KuCoinWS(WSConnection):
    """KuCoin Futures WebSocket (public).

    KuCoin requires a token first via REST, then connect to the provided endpoint.

    TWO topic subscriptions per symbol (unlike Bybit's single topic):
      - /contract/instrument:{symbol}  -- markPrice (subject "mark.index.price")
        and fundingRate (subject "funding.rate"). NOTE: the topic name used
        here previously was "/contract/ticker:{symbol}", which does not
        match any topic in KuCoin's published Futures WS docs -- see "Bug A"
        at the top of this document. This fixes that.
      - /contractMarket/tickerV2:{symbol} -- bestBidPrice/bestAskPrice. This
        is the NEW subscription added for bid/ask spread math.
    """

    def __init__(self, symbols: list[str],
                 price_cache: PriceCache,
                 funding_cache: FundingCache,
                 on_spread_update: Optional[MessageCallback] = None):
        self._symbols = symbols
        self._price_cache = price_cache
        self._funding_cache = funding_cache
        self._on_spread_update = on_spread_update
        self._sub_id_counter = 0

        url = self._get_ws_url()
        if url is None:
            url = "wss://ws-futures.kucoin.com/endpoint"
        super().__init__("kucoin", url, on_message=self._handle_message)

    def _get_ws_url(self) -> Optional[str]:
        with get_limiter("kucoin", 10):
            try:
                r = requests.post(
                    "https://api-futures.kucoin.com/api/v1/bullet-public",
                    timeout=10,
                )
                if r.ok:
                    d = r.json()
                    if d.get("code") == "200000":
                        instance = d["data"]["instanceServers"][0]
                        token = d["data"]["token"]
                        return f"{instance['endpoint']}?token={token}&connectId=frbot-{int(time.time())}"
            except Exception:
                log.warning("KuCoin WS: failed to get bullet token, using default endpoint")
        return None

    def _refresh_url(self):
        fresh = self._get_ws_url()
        if fresh:
            self.url = fresh
        else:
            log.warning("[kucoin] Could not refresh bullet token -- retrying with previous URL")

    def update_symbols(self, symbols: list[str]):
        self._symbols = symbols
        if self.is_connected():
            self._subscribe_topics(self._ws)

    def _next_sub_id(self) -> str:
        self._sub_id_counter += 1
        return f"{int(time.time() * 1000)}-{self._sub_id_counter}"

    def _subscribe_topics(self, ws):
        for sym in self._symbols:
            base = f"{sym}USDTM"

            sub_instrument = json.dumps({
                "id": self._next_sub_id(),
                "type": "subscribe",
                "topic": f"/contract/instrument:{base}",
                "response": False,
            })
            ws.send(sub_instrument)

            sub_ticker = json.dumps({
                "id": self._next_sub_id(),
                "type": "subscribe",
                "topic": f"/contractMarket/tickerV2:{base}",
                "response": False,
            })
            ws.send(sub_ticker)
        log.info("[kucoin] Subscribed to %d symbols (instrument + tickerV2, %d topics total)",
                 len(self._symbols), len(self._symbols) * 2)

    def _handle_message(self, exchange: str, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "message" or "topic" not in msg:
            if msg.get("type") == "welcome":
                log.info("[kucoin] WS welcome received")
            return

        topic = msg["topic"]
        data = msg.get("data", {})
        if not data:
            return

        if "/contract/instrument:" in topic:
            self._handle_instrument(topic, msg.get("subject", ""), data)
        elif "/contractMarket/tickerV2:" in topic:
            self._handle_ticker_v2(topic, data)

    def _handle_instrument(self, topic: str, subject: str, data: dict):
        from exchanges.symbols import kucoin_to_unified
        raw_sym = topic.split(":")[-1]
        unified = kucoin_to_unified(raw_sym)

        if subject == "mark.index.price":
            mark = _safe_float(data.get("markPrice"))
            if mark is not None:
                self._price_cache.update("kucoin", unified, mark=mark)
                self._on_spread_update and self._on_spread_update("kucoin", "price", {"symbol": unified})
            return

        if subject == "funding.rate":
            fr = _safe_float(data.get("fundingRate"))
            if fr is None:
                return
            nft = _safe_int(data.get("nextFundingRateTime")) or _safe_int(data.get("timestamp"))
            existing = self._funding_cache.get(unified, "kucoin")
            interval_h = existing.get("interval_h", 8) if existing else 8
            self._funding_cache.update("kucoin", unified, fr, fr, nft, max(int(interval_h), 1))
            self._on_spread_update and self._on_spread_update("kucoin", "funding", {
                "symbol": unified, "funding_rate": fr, "next_funding_ts": nft,
            })

    def _handle_ticker_v2(self, topic: str, data: dict):
        from exchanges.symbols import kucoin_to_unified
        raw_sym = topic.split(":")[-1]
        unified = kucoin_to_unified(raw_sym)

        bid = _safe_float(data.get("bestBidPrice"))
        ask = _safe_float(data.get("bestAskPrice"))
        if bid is not None or ask is not None:
            self._price_cache.update("kucoin", unified, bid=bid, ask=ask)
            self._on_spread_update and self._on_spread_update("kucoin", "price", {"symbol": unified})


class WSPool:
    """Manages both exchange connections + delegates messages to caches."""

    def __init__(self, price_cache: PriceCache, funding_cache: FundingCache,
                 on_spread_update: Optional[MessageCallback] = None):
        self.price_cache = price_cache
        self.funding_cache = funding_cache
        self._on_spread_update = on_spread_update
        self.bybit: Optional[BybitWS] = None
        self.kucoin: Optional[KuCoinWS] = None

    def start(self, symbols: list[str]):
        syms = symbols or []
        self.bybit = BybitWS(syms, self.price_cache, self.funding_cache, self._on_spread_update)
        self.kucoin = KuCoinWS(syms, self.price_cache, self.funding_cache, self._on_spread_update)
        self.bybit.start()
        time.sleep(0.5)
        self.kucoin.start()
        log.info("WSPool started with %d symbols", len(syms))

    def update_symbols(self, symbols: list[str]):
        if self.bybit:
            self.bybit.update_symbols(symbols)
        if self.kucoin:
            self.kucoin.update_symbols(symbols)

    def stop(self):
        if self.bybit:
            self.bybit.stop()
        if self.kucoin:
            self.kucoin.stop()
        log.info("WSPool stopped")

    def is_ready(self) -> bool:
        if not self.bybit or not self.kucoin:
            return False
        return self.bybit.is_connected() and self.kucoin.is_connected()


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
