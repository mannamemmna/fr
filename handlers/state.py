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
    from core.rebalance_engine import RebalanceEngine

# ─── Engine instances (set by bot.py on startup) ─────────────────────
paper_engine: "PaperEngine | None" = None
auto_engine: "AutomationEngine | None" = None
price_cache: "PriceCache | None" = None
funding_cache: "FundingCache | None" = None
ws_pool: "WSPool | None" = None
spread_engine: "SpreadEngine | None" = None
db: "LocalDB | None" = None
rebalance_engine: "RebalanceEngine | None" = None

# ─── Runtime state (updated by handlers and engine callbacks) ────────
last_scan: dict = {}
exchange_health: dict = {"bybit": True, "kucoin": True}