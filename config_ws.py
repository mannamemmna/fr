"""WebSocket-specific config — imported by ws_pool and other modules.

Reads from .env via config.py so there's one source of truth.
"""

from config import WS_HEARTBEAT_SEC, REST_RATE_LIMIT_PER_SEC, DB_PATH

# ─── Reconnect backoff ───
WS_RECONNECT_BASE: float = 1.0
WS_RECONNECT_MAX: float = 60.0
WS_RECONNECT_JITTER: float = 0.5

# ─── Rate limiter warning threshold ───
REST_RATE_LIMIT_WARN_PCT: int = 80