# Stub for exchanges/keys_helper.py compatibility
# Reads API keys from os.environ instead of encrypted storage

from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET,
    KUCOIN_API_KEY, KUCOIN_API_SECRET,
    PAPER_MODE,
)

_KEY_MAP = {
    "bybit": (BYBIT_API_KEY, BYBIT_API_SECRET),
    "kucoin": (KUCOIN_API_KEY, KUCOIN_API_SECRET),
}


def has_api_key(exchange_name: str) -> bool:
    """Check if exchange has API keys configured in env."""
    if PAPER_MODE:
        return False  # paper mode = no real exchange access needed
    key, secret = _KEY_MAP.get(exchange_name.strip().lower(), ("", ""))
    return bool(key and secret)


def load_api_key(exchange_name: str) -> tuple[str, str]:
    """Load API key + secret from env vars."""
    key, secret = _KEY_MAP.get(exchange_name.strip().lower(), ("", ""))
    if not key or not secret:
        raise ValueError(f"No API keys in .env for '{exchange_name}'")
    return key, secret


def save_api_key(*args, **kwargs):
    raise NotImplementedError("API keys are managed via .env, not encrypted storage")


def delete_api_key(*args, **kwargs):
    raise NotImplementedError("API keys are managed via .env, not encrypted storage")


def list_configured_exchanges() -> list[str]:
    return [name for name, (k, s) in _KEY_MAP.items() if k and s]


def init_encryption(*args, **kwargs):
    pass  # no-op: not using encryption in fr-bot


def is_initialised() -> bool:
    return True


def load_api_passphrase(*args, **kwargs):
    return None


def reset_storage(*args, **kwargs):
    pass
