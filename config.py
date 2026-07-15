"""
FR Bot configuration — reads from .env file.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# ─── Paths ───
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─── Bot ───
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
# Telegram chat ID for all notifications (auto events, errors, health)
# Find it: DM @userinfobot → "id"
NOTIFY_CHAT_ID: str = os.getenv("NOTIFY_CHAT_ID", "")

# ─── Mode ───
PAPER_MODE: bool = os.getenv("PAPER_MODE", "true").lower() in ("true", "1", "yes")
PAPER_INITIAL_BALANCE: float = float(os.getenv("PAPER_INITIAL_BALANCE", "10000"))

# ─── Exchange API Keys (env-based — used when PAPER_MODE=false) ───
BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET: str = os.getenv("BYBIT_API_SECRET", "")
KUCOIN_API_KEY: str = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET: str = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE: str = os.getenv("KUCOIN_API_PASSPHRASE", "")
# Double-confirmation guard for real orders. Required when PAPER_MODE=false.
LIVE_CONFIRM: bool = os.getenv("LIVE_CONFIRM", "false").lower() in ("true", "1", "yes")

# ─── Auto-scan ───
# NOTE: auto_engine does its own scanning; this is for the background scanner (REST fallback)
AUTO_SCAN_INTERVAL: int = int(os.getenv("AUTO_SCAN_INTERVAL", 60))

# ─── WebSocket ───
WS_HEARTBEAT_SEC: int = int(os.getenv("WS_HEARTBEAT_SEC", "20"))
REST_RATE_LIMIT_PER_SEC: int = int(os.getenv("REST_RATE_LIMIT_PER_SEC", "10"))
DB_PATH: str = str(os.getenv("DB_PATH", "fr-bot.db"))

# Bybit/KuCoin WS connections become unstable (disconnect loop) with too
# many subscribed tickers on one connection. Applies everywhere a symbol
# list is subscribed: initial bootstrap (bot.py), background scanner
# (core/bg_scanner.py), and manual /scan (handlers/scan.py) — kept as one
# constant so all three stay in sync.
MAX_WS_SUBSCRIPTIONS: int = int(os.getenv("MAX_WS_SUBSCRIPTIONS", "100"))

# ─── Leverage ───
DEFAULT_LEVERAGE: int = int(os.getenv("DEFAULT_LEVERAGE", "2"))

# ─── Automation Config ───
AUTO_MODE: bool = str(os.getenv("AUTO_MODE", "false")).lower() == "true"
AUTO_LEVERAGE: int = int(os.getenv("AUTO_LEVERAGE", "3"))
AUTO_BALANCE_PER_LEG: float = float(os.getenv("AUTO_BALANCE_PER_LEG", "1000"))
AUTO_MAX_POSITIONS: int = int(os.getenv("AUTO_MAX_POSITIONS", "1"))
AUTO_MONITOR_INTERVAL: float = float(os.getenv("AUTO_MONITOR_INTERVAL", "0.5"))
AUTO_ENTRY_WINDOW_MIN: int = int(os.getenv("AUTO_ENTRY_WINDOW_MIN", "30"))
AUTO_DELTA_THRESHOLD: float = float(os.getenv("AUTO_DELTA_THRESHOLD", "0.4"))  # Min Diff FR untuk LOOKING
AUTO_DELAY_CANCEL_FUNDING_DIFF: float = float(os.getenv("AUTO_DELAY_CANCEL_FUNDING_DIFF", "0.2"))
AUTO_DELAY_ENTRY_PRICE_SPREAD: float = float(os.getenv("AUTO_DELAY_ENTRY_PRICE_SPREAD", "0.0"))  # Entry jika spread <= nilai ini

AUTO_LIVE_CLOSE_FUNDING_DIFF: float = float(os.getenv("AUTO_LIVE_CLOSE_FUNDING_DIFF", "0.05"))   # Tahap 1: tunggu Diff FR drop
AUTO_LIVE_CLOSE_PRICE_SPREAD: float = float(os.getenv("AUTO_LIVE_CLOSE_PRICE_SPREAD", "0.0"))    # Tahap 2: close jika spread >= nilai ini

# Auto-close all open paper positions on bot restart (prevents unmonitored floating)
AUTO_CLOSE_ON_RESTART: bool = os.getenv("AUTO_CLOSE_ON_RESTART", "true").lower() in ("true", "1", "yes")

# ─── Rebalance ───
REBALANCE_THRESHOLD: float = float(os.getenv("REBALANCE_THRESHOLD", "0.40"))
REBALANCE_PAPER_FEE_PCT: float = float(os.getenv("REBALANCE_PAPER_FEE_PCT", "0.001"))
REBALANCE_PAPER_DELAY_SEC: int = int(os.getenv("REBALANCE_PAPER_DELAY_SEC", "5"))
REBALANCE_CHECK_INTERVAL_SEC: int = int(os.getenv("REBALANCE_CHECK_INTERVAL_SEC", "60"))
REBALANCE_AUTO_TRANSFER: bool = os.getenv("REBALANCE_AUTO_TRANSFER", "false").lower() in ("true", "1", "yes")

# ─── Live CEX-to-CEX Withdrawal (real fund movement) ───
REBALANCE_LIVE_TRANSFER_ENABLED: bool = os.getenv("REBALANCE_LIVE_TRANSFER_ENABLED", "false").lower() in ("true", "1", "yes")
REBALANCE_LIVE_DRY_RUN: bool = os.getenv("REBALANCE_LIVE_DRY_RUN", "true").lower() in ("true", "1", "yes")

REBALANCE_TOKEN: str = os.getenv("REBALANCE_TOKEN", "USDT")
REBALANCE_NETWORK: str = os.getenv("REBALANCE_NETWORK", "TRON")  # TRON | BSC | BASE | ARBITRUM

# Destination = address on the RECEIVING exchange for that network.
REBALANCE_BYBIT_DEPOSIT_ADDRESS: str = os.getenv("REBALANCE_BYBIT_DEPOSIT_ADDRESS", "")
REBALANCE_KUCOIN_DEPOSIT_ADDRESS: str = os.getenv("REBALANCE_KUCOIN_DEPOSIT_ADDRESS", "")
# Optional memo/tag (not needed for TRON/BSC/BASE/ARBITRUM USDT, kept for future chains)
REBALANCE_BYBIT_DEPOSIT_MEMO: str = os.getenv("REBALANCE_BYBIT_DEPOSIT_MEMO", "")
REBALANCE_KUCOIN_DEPOSIT_MEMO: str = os.getenv("REBALANCE_KUCOIN_DEPOSIT_MEMO", "")

# Hard safety caps — withdrawal request rejected client-side if outside range.
REBALANCE_MIN_TRANSFER_USD: float = float(os.getenv("REBALANCE_MIN_TRANSFER_USD", "20"))
REBALANCE_MAX_TRANSFER_USD: float = float(os.getenv("REBALANCE_MAX_TRANSFER_USD", "500"))

REBALANCE_WITHDRAW_POLL_INTERVAL_SEC: float = float(os.getenv("REBALANCE_WITHDRAW_POLL_INTERVAL_SEC", "15"))
REBALANCE_WITHDRAW_POLL_TIMEOUT_SEC: float = float(os.getenv("REBALANCE_WITHDRAW_POLL_TIMEOUT_SEC", "1800"))

# ─── Internal transfer after deposit (Bybit UTA 2.0 / KuCoin Futures) ───
REBALANCE_DEPOSIT_POLL_INTERVAL_SEC: float = float(os.getenv("REBALANCE_DEPOSIT_POLL_INTERVAL_SEC", "10"))
REBALANCE_DEPOSIT_POLL_TIMEOUT_SEC: float = float(os.getenv("REBALANCE_DEPOSIT_POLL_TIMEOUT_SEC", "1800"))
REBALANCE_INTERNAL_TRANSFER_POLL_INTERVAL_SEC: float = float(os.getenv("REBALANCE_INTERNAL_TRANSFER_POLL_INTERVAL_SEC", "5"))
REBALANCE_INTERNAL_TRANSFER_POLL_TIMEOUT_SEC: float = float(os.getenv("REBALANCE_INTERNAL_TRANSFER_POLL_TIMEOUT_SEC", "600"))

# ─── Hedge Integrity ───
HEDGE_EMERGENCY_OPEN: bool = os.getenv("HEDGE_EMERGENCY_OPEN", "true").lower() in ("true", "1", "yes")
HEDGE_CHECK_INTERVAL_SEC: int = int(os.getenv("HEDGE_CHECK_INTERVAL_SEC", "30"))
HEDGE_BALANCE_DROP_THRESHOLD: float = float(os.getenv("HEDGE_BALANCE_DROP_THRESHOLD", "0.95"))

# ─── Live Engine — Order Fill Verification & Partial Fill Protection ───
LIVE_ORDER_PLACEMENT_MAX_RETRIES: int = int(os.getenv("LIVE_ORDER_PLACEMENT_MAX_RETRIES", "3"))
LIVE_ORDER_PLACEMENT_RETRY_BASE_SEC: float = float(os.getenv("LIVE_ORDER_PLACEMENT_RETRY_BASE_SEC", "1.0"))
LIVE_FILL_POLL_INTERVAL_SEC: float = float(os.getenv("LIVE_FILL_POLL_INTERVAL_SEC", "0.5"))
LIVE_FILL_POLL_TIMEOUT_SEC: float = float(os.getenv("LIVE_FILL_POLL_TIMEOUT_SEC", "10"))
LIVE_PARTIAL_FILL_TOLERANCE_PCT: float = float(os.getenv("LIVE_PARTIAL_FILL_TOLERANCE_PCT", "0.02"))
LIVE_PARTIAL_FILL_TOPUP_MAX_ATTEMPTS: int = int(os.getenv("LIVE_PARTIAL_FILL_TOPUP_MAX_ATTEMPTS", "2"))

# ─── Live Engine — Unrealized PnL Display ───
LIVE_UNREALIZED_PNL_ENABLED: bool = os.getenv("LIVE_UNREALIZED_PNL_ENABLED", "true").lower() in ("true", "1", "yes")

# ─── Live Engine — Diff Interval Exit Strategy ───
# Max hold time (menit) untuk Jalur A (interval beda) — force exit sebelum
# kena bleeding FR (misal 10 menit sebelum SHORT exchange bayar lagi).
LIVE_DIFF_HOLD_MAX_MINUTES: int = int(os.getenv("LIVE_DIFF_HOLD_MAX_MINUTES", "40"))

# ─── Delisting Protection ───
DELISTING_MONITOR_ENABLED: bool = os.getenv("DELISTING_MONITOR_ENABLED", "true").lower() in ("true", "1", "yes")
DELISTING_CHECK_INTERVAL_SEC: int = int(os.getenv("DELISTING_CHECK_INTERVAL_SEC", "3600"))
# HATI-HATI: auto-close posisi terbuka begitu delisting terdeteksi (bukan cuma
# alert). Default false — parsing judul itu best-effort, false positive bisa
# menutup posisi yang sebenarnya aman. Aktifkan hanya kalau paham risikonya.
AUTO_CLOSE_ON_DELISTING_DETECTED: bool = os.getenv("AUTO_CLOSE_ON_DELISTING_DETECTED", "false").lower() in ("true", "1", "yes")

# ─── Delisting Protection — in-memory cache TTL for automation loop ───
# Automation engine hits the DB less often by caching the blacklist set for
# this many seconds. Independent from DELISTING_CHECK_INTERVAL_SEC (which
# controls how often we poll exchanges for NEW announcements).
DELISTING_BLACKLIST_CACHE_TTL_SEC: int = int(os.getenv("DELISTING_BLACKLIST_CACHE_TTL_SEC", "30"))

# ─── Display ───
DEFAULT_TOP_N: int = int(os.getenv("DEFAULT_TOP_N", "10"))
