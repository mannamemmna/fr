"""WebSocket Connection Pool — Bybit + KuCoin real-time market data.

Architecture:
    ws_pool  ──callback──→  price_cache + funding_cache  ──→  spread_engine

Each exchange gets its own connection. Auto-reconnect with exponential backoff.
All subscriptions are re-sent on reconnect.
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
)
from config import WS_HEARTBEAT_SEC
from exchanges.bybit import BybitClient
from exchanges.kucoin import KuCoinClient
from core.market_cache import PriceCache, FundingCache
from core.rate_limiter import get_limiter

log = logging.getLogger("fr-bot.ws")

# ─── Callback type ──────────────────────────────────────────────────────────
# Called on every meaningful message: (exchange, type, data)
# type: 'price', 'funding', 'ticker'
MessageCallback = Callable[[str, str, dict], None]

# ─── WebSocket connection ────────────────────────────────────────────────────


class WSConnection:
    """Single WebSocket connection to one exchange with auto-reconnect.

    Uses `websocket.WebSocketApp` in a daemon thread.
    """

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
        self._subscribed_topics: list[str] = []

    # ─── Public API ───────────────────────────────────────────────────

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
        """Set subscription topics. Sent on next (re)connect."""
        self._subscribed_topics = topics

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ─── Internal ─────────────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            try:
                self._connect_and_listen()
            except Exception as e:
                log.error("[%s] WS error: %s", self.name, e)
            if self._stop.is_set():
                break
            self._backoff()
        log.info("[%s] WS run loop ended", self.name)

    def _connect_and_listen(self):
        self._ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_ws_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # Run in blocking mode — will block until disconnected
        self._ws.run_forever(
            ping_interval=WS_HEARTBEAT_SEC,
            ping_timeout=5,
            skip_utf8_validation=False,
        )

    def _on_open(self, ws):
        self._connected.set()
        self._reconnect_count = 0
        log.info("[%s] WS connected", self.name)
        # Re-subscribe
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
        """Override in subclass."""
        pass

    def send(self, data):
        """Send raw message. Safe thread."""
        if self._ws and self._connected.is_set():
            try:
                self._ws.send(data)
            except Exception:
                pass


# ─── Bybit WebSocket ────────────────────────────────────────────────────────

class BybitWS(WSConnection):
    """Bybit V5 public linear WebSocket.

    Docs: https://bybit-exchange.github.io/docs/v5/ws/connect
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

        # Bybit ticker push
        if msg.get("topic", "").startswith(self.TICKER_BASE):
            data = msg.get("data", {})
            if not data:
                return
            unified = self._topic_to_unified(msg["topic"])
            if not unified:
                return

            mark = _safe_float(data.get("markPrice"))
            if mark and mark > 0:
                self._price_cache.update("bybit", unified, mark)

            fr = _safe_float(data.get("fundingRate"))
            if fr is not None:
                nft = _safe_int(data.get("nextFundingTime"))
                interval = _safe_int(data.get("fundingIntervalHour")) or 8
                self._funding_cache.update(
                    "bybit", unified, fr, fr, nft, interval,
                )
                self._on_spread_update and self._on_spread_update("bybit", "funding", {
                    "symbol": unified, "funding_rate": fr,
                    "next_funding_ts": nft, "interval_h": interval,
                })

    def _topic_to_unified(self, topic: str) -> Optional[str]:
        prefix = self.TICKER_BASE
        if not topic.startswith(prefix):
            return None
        raw = topic[len(prefix):]
        # "BTCUSDT" -> "BTC/USDT:USDT"
        from exchanges.symbols import bybit_to_unified
        return bybit_to_unified(raw)


# ─── KuCoin WebSocket ───────────────────────────────────────────────────────

class KuCoinWS(WSConnection):
    """KuCoin Futures WebSocket (public).

    KuCoin requires a token first via REST, then connect to the provided endpoint.
    """

    def __init__(self, symbols: list[str],
                 price_cache: PriceCache,
                 funding_cache: FundingCache,
                 on_spread_update: Optional[MessageCallback] = None):
        self._symbols = symbols
        self._price_cache = price_cache
        self._funding_cache = funding_cache
        self._on_spread_update = on_spread_update
        self._pings_sent = 0

        url = self._get_ws_url()
        if url is None:
            url = "wss://ws-futures.kucoin.com/endpoint"
        super().__init__("kucoin", url, on_message=self._handle_message)

    def _get_ws_url(self) -> Optional[str]:
        """Obtain KuCoin WebSocket endpoint via REST bullet API."""
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

    def update_symbols(self, symbols: list[str]):
        self._symbols = symbols
        if self.is_connected():
            self._subscribe_topics(self._ws)

    def _subscribe_topics(self, ws):
        for sym in self._symbols:
            topic = f"/contract/ticker:{sym}USDTM"
            sub = json.dumps({
                "id": str(int(time.time() * 1000)),
                "type": "subscribe",
                "topic": topic,
                "response": False,
            })
            ws.send(sub)
        log.info("[kucoin] Subscribed to %d tickers", len(self._symbols))

    def _handle_message(self, exchange: str, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # KuCoin ticker push
        if msg.get("type") == "message" and "topic" in msg:
            topic = msg["topic"]
            if "/contract/ticker:" in topic:
                raw_sym = topic.split(":")[-1]
                data = msg.get("data", {})
                if not data:
                    return
                from exchanges.symbols import kucoin_to_unified
                unified = kucoin_to_unified(raw_sym)

                mark = _safe_float(data.get("markPrice"))
                if mark and mark > 0:
                    self._price_cache.update("kucoin", unified, mark)

                fr = _safe_float(data.get("fundingRate"))
                if fr is not None:
                    npr = _safe_float(data.get("predictedFundingFeeRate")) or fr
                    nft = _safe_int(data.get("nextFundingRateTime"))
                    # Bug fix: jangan hitung interval dari time-to-next-payment.
                    # Gunakan interval_h yang sudah ada dari REST scan (fundingRateGranularity).
                    # Fallback ke 8 jika belum ada data sebelumnya.
                    existing = self._funding_cache.get(unified, "kucoin")
                    interval_h = existing.get("interval_h", 8) if existing else 8
                    self._funding_cache.update(
                        "kucoin", unified, fr, npr, nft, max(int(interval_h), 1),
                    )
                    self._on_spread_update and self._on_spread_update("kucoin", "funding", {
                        "symbol": unified, "funding_rate": fr,
                        "next_funding_ts": nft,
                    })
        # KuCoin welcome/pong — ignore
        elif msg.get("type") == "welcome":
            log.info("[kucoin] WS welcome received")
        elif msg.get("type") == "pong":
            pass


# ─── Connection Pool ────────────────────────────────────────────────────────

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
        """Start both WebSocket connections with subscribed symbols."""
        syms = symbols or []
        self.bybit = BybitWS(syms, self.price_cache, self.funding_cache, self._on_spread_update)
        self.kucoin = KuCoinWS(syms, self.price_cache, self.funding_cache, self._on_spread_update)
        self.bybit.start()
        # Small delay so KuCoin token request is not blocked
        time.sleep(0.5)
        self.kucoin.start()
        log.info("WSPool started with %d symbols", len(syms))

    def update_symbols(self, symbols: list[str]):
        """Resubscribe with new symbol list."""
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


# ─── Helpers ────────────────────────────────────────────────────────────────

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