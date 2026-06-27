"""Token bucket rate limiter for REST API calls.

Tracks usage, logs warning when approaching limit,
and blocks when exceeded. Thread-safe.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("fr-bot.rate_limiter")

DEFAULT_RATE = 10  # calls/second
WARN_THRESHOLD = 0.80  # warn when 80% of capacity used


class RateLimiter:
    """Token bucket per-name limiter.

    Usage:
        limiter = RateLimiter("bybit", 10)
        with limiter:
            requests.get(...)
    """

    def __init__(self, name: str, rate: float = DEFAULT_RATE, warn_pct: float = WARN_THRESHOLD):
        self.name = name
        self.rate = rate  # tokens per second
        self.warn_pct = warn_pct
        self._tokens = rate
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._total_calls = 0
        self._blocked_calls = 0

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def acquire(self, block: bool = True) -> bool:
        """Take one token. Returns True if allowed, False if blocked."""
        with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                self._total_calls += 1
                usage = self._tokens / self.rate
                if usage >= self.warn_pct:
                    log.warning("[%s] Rate limit usage at %.0f%%", self.name, usage * 100)
                return True
            self._blocked_calls += 1
            if block:
                sleep_time = 1.0 / self.rate
                log.debug("[%s] Rate limited, sleeping %.2fs", self.name, sleep_time)
                time.sleep(sleep_time)
                self._tokens = self.rate  # reset after sleep
                self._tokens -= 1
                return True
            return False

    def __enter__(self):
        self.acquire(block=True)
        return self

    def __exit__(self, *args):
        pass

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "rate": self.rate,
                "total_calls": self._total_calls,
                "blocked_calls": self._blocked_calls,
                "current_tokens": round(self._tokens, 2),
            }


# ─── Global instances ──────────────────────────────────────────────────────

_instances: dict[str, RateLimiter] = {}
_lock = threading.Lock()


def get_limiter(name: str, rate: float = DEFAULT_RATE) -> RateLimiter:
    with _lock:
        if name not in _instances:
            _instances[name] = RateLimiter(name, rate)
        return _instances[name]


def all_stats() -> list[dict]:
    return [l.stats for l in _instances.values()]