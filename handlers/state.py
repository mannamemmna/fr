"""Shared mutable state for handler modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.paper_engine import PaperEngine
    from core.automation_engine import AutomationEngine
    from core.ws_pool import WSPool
    from core.spread_engine import SpreadEngine
    from core.market_cache import PriceCache, FundingCache
    from core.db import LocalDB

# ─── Engine instances (set by bot.py on startup) ─────────────────────
paper_engine: "PaperEngine | None" = None
auto_engine: "AutomationEngine | None" = None
price_cache: "PriceCache | None" = None
funding_cache: "FundingCache | None" = None
ws_pool: "WSPool | None" = None
spread_engine: "SpreadEngine | None" = None
db: "LocalDB | None" = None

# ─── Runtime state (updated by handlers and engine callbacks) ────────
last_scan: dict = {}
exchange_health: dict = {"bybit": True, "kucoin": True}

# Read by core/bg_scanner.py's _send_alert() and written by bot.py (only
# when NOTIFY_CHAT_ID is set) and handlers/auto.py's "/auto on". Must have
# a default here — without one, _send_alert() raises an uncaught
# AttributeError the first time it's called while NOTIFY_CHAT_ID is unset,
# which silently kills the background scanner's daemon thread (auto-scan
# stops forever until the bot process is restarted; no crash visible to
# the operator, Telegram commands keep responding normally).
_notify_chat_id: str | None = None