"""WebSocket-specific config — imported by ws_pool and other modules.

Reads from .env via config.py so there's one source of truth.
"""

from config import WS_HEARTBEAT_SEC, REST_RATE_LIMIT_PER_SEC, DB_PATH

# ─── Reconnect backoff ───
WS_RECONNECT_BASE: float = 1.0
WS_RECONNECT_MAX: float = 60.0
WS_RECONNECT_JITTER: float = 0.5

# Minimum detik koneksi harus bertahan sebelum reconnect-counter direset
# ke 0. Mencegah koneksi yang diterima-lalu-langsung-ditolak (mis. token
# basi) mereset attempt-count tiap siklus dan nge-hammer server di interval
# dasar terus-menerus.
WS_MIN_STABLE_SEC: float = 10.0

# ─── Rate limiter warning threshold ───
REST_RATE_LIMIT_WARN_PCT: int = 80