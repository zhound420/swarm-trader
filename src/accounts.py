"""
Account routing for multi-account Alpaca setup.

Two accounts:
  - "swing" (Primary):     ALPACA_API_KEY / ALPACA_API_SECRET — multi-day holds
  - "day"   (DayTrading):  ALPACA_DAY_API_KEY / ALPACA_DAY_API_SECRET — intraday, flattens EOD

Credentials are selected based on trading mode. All API helpers accept
an optional `mode` parameter; if omitted, the current trading mode
(from trading_mode.json) determines which account to use.

If only the primary account is configured, both modes share it (backward compatible).
"""

import os
from dataclasses import dataclass


@dataclass
class AlpacaAccount:
    """Credentials + metadata for a single Alpaca account."""
    name: str
    account_id: str
    api_key: str
    api_secret: str
    base_url: str = "https://paper-api.alpaca.markets/v2"

    @property
    def headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }


# Account registry — loaded from env vars
_ACCOUNTS: dict[str, AlpacaAccount] = {}


def _load_accounts() -> None:
    """Load account credentials from environment. Called once on import."""
    global _ACCOUNTS

    # Swing account (original, has existing positions ~$148K)
    swing_key = os.environ.get("ALPACA_API_KEY", "")
    swing_secret = os.environ.get("ALPACA_API_SECRET", "")
    if swing_key and swing_secret:
        _ACCOUNTS["swing"] = AlpacaAccount(
            name="Swing",
            account_id="PA3JDEMM789Z",
            api_key=swing_key,
            api_secret=swing_secret,
        )

    # Day trading account (fresh $100K)
    day_key = os.environ.get("ALPACA_DAY_API_KEY", "")
    day_secret = os.environ.get("ALPACA_DAY_API_SECRET", "")
    if day_key and day_secret:
        _ACCOUNTS["day"] = AlpacaAccount(
            name="DayTrading",
            account_id="PA3NVVU2WEOH",
            api_key=day_key,
            api_secret=day_secret,
        )


def get_account_for_mode(mode: str = None) -> AlpacaAccount:
    """
    Get the correct Alpaca account for the given trading mode.

    Args:
        mode: "swing" or "day". If None, resolves from trading_mode.json.

    Returns:
        AlpacaAccount with credentials for the appropriate account.

    Raises:
        ValueError if the account for the given mode is not configured.
    """
    if not _ACCOUNTS:
        _load_accounts()

    if mode is None:
        from src.config import resolve_mode
        mode = resolve_mode()
        if mode == "auto":
            mode = "swing"  # safe default

    mode = mode.lower()

    if mode in _ACCOUNTS:
        return _ACCOUNTS[mode]

    # Fallback: if swing account isn't configured yet, use day account
    if mode == "swing" and "day" in _ACCOUNTS:
        return _ACCOUNTS["day"]

    if "day" in _ACCOUNTS:
        return _ACCOUNTS["day"]

    raise ValueError(
        f"No Alpaca account configured for mode '{mode}'. "
        "Check ALPACA_API_KEY / ALPACA_SWING_API_KEY in .env"
    )


def get_all_accounts() -> dict[str, AlpacaAccount]:
    """Return all configured accounts."""
    if not _ACCOUNTS:
        _load_accounts()
    return dict(_ACCOUNTS)


# Auto-load on import
_load_accounts()
