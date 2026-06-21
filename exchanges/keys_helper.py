"""API key storage helper (lazy import to avoid circular deps)."""

from typing import Tuple

from core.keys import has_api_key, load_api_key


def _build_ccxt_config(exchange_name: str) -> dict:
    """Build ccxt config for *exchange_name*, loading keys if present."""
    cfg: dict = {
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    }
    if has_api_key(exchange_name):
        try:
            api_key, api_secret = load_api_key(exchange_name)
            cfg["apiKey"] = api_key
            cfg["secret"] = api_secret
        except Exception:  # noqa: BLE001
            pass
    return cfg
