"""Shared mutable state for handler modules."""

from __future__ import annotations

from core.paper_engine import PaperEngine
from core.automation_engine import AutomationEngine

paper_engine: PaperEngine | None = None
last_scan: dict = {}
auto_engine: AutomationEngine | None = None
_notify_chat_id: str | None = None
exchange_health = {"bybit": True, "kucoin": True}
