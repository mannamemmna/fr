"""Shared mutable state for handler modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.paper_engine import PaperEngine
    from core.automation_engine import AutomationEngine
    from core.market_cache import PriceCache, FundingCache
    from core.ws_pool import WSPool
    from core.spread_engine import SpreadEngine
    from core.db import LocalDB

# ─── Engine singletons (set at startup) ───
paper_engine: PaperEngine | None = None
auto_engine: AutomationEngine | None = None
last_scan: dict = {}
_notify_chat_id: str | None = None
exchange_health = {"bybit": True, "kucoin": True}

# ─── WebSocket / Cache / DB (new architecture) ───
price_cache: PriceCache | None = None
funding_cache: FundingCache | None = None
ws_pool: WSPool | None = None
spread_engine: SpreadEngine | None = None
db: LocalDB | None = None