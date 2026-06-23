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
# NOTE: auto_engine does its own scanning; this is for the background scanner
AUTO_SCAN_INTERVAL: int = int(os.getenv("AUTO_SCAN_INTERVAL", 60))

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

AUTO_PREFER_SAME_INTERVAL: bool = os.getenv("AUTO_PREFER_SAME_INTERVAL", "true").lower() in ("true", "1", "yes")
# Auto-close all open paper positions on bot restart (prevents unmonitored floating)
AUTO_CLOSE_ON_RESTART: bool = os.getenv("AUTO_CLOSE_ON_RESTART", "true").lower() in ("true", "1", "yes")

# ─── Display ───
DEFAULT_TOP_N: int = int(os.getenv("DEFAULT_TOP_N", "10"))
