"""
live/market_data_router.py

Unified market-data routing — mirrors execution routing in broker_router.py.

  crypto + coinbase_credentials_ready → CoinbaseBrokerAdapter
  forex  + oanda_credentials_ready     → OandaBrokerAdapter
  else                                 → PolygonBrokerAdapter (or none)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from config.coinbase_symbols import is_coinbase_tradable
from config.execution_config import coinbase_credentials_ready, oanda_credentials_ready
from config.oanda_symbols import is_oanda_tradable
from config.settings import Settings, get_settings

if TYPE_CHECKING:
    from live.broker_adapter import BrokerAdapter

logger = logging.getLogger(__name__)

_ADAPTER_CACHE: dict[str, BrokerAdapter] = {}


def resolve_market_data_broker_id(
    symbol: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Return market-data broker id for a symbol (coinbase | oanda | polygon | none)."""
    settings = settings or get_settings()
    sym = symbol.upper()

    if is_coinbase_tradable(sym) and coinbase_credentials_ready(settings):
        return "coinbase"
    if is_oanda_tradable(sym) and oanda_credentials_ready(settings):
        return "oanda"
    if os.getenv("POLYGON_API_KEY", "").strip():
        return "polygon"
    return "none"


def resolve_market_data_adapter(
    symbol: str,
    *,
    settings: Settings | None = None,
) -> BrokerAdapter:
    """Pick the market-data adapter for a symbol (cached singleton per broker id)."""
    from live.broker_adapter import NullBrokerAdapter, get_broker_adapter

    broker_id = resolve_market_data_broker_id(symbol, settings=settings)
    if broker_id == "none":
        return NullBrokerAdapter()

    if broker_id not in _ADAPTER_CACHE:
        _ADAPTER_CACHE[broker_id] = get_broker_adapter(broker_id)
        logger.debug(
            "market_data_router: %s → %s adapter",
            symbol.upper(),
            broker_id,
        )
    return _ADAPTER_CACHE[broker_id]


def clear_market_data_adapter_cache() -> None:
    """Test helper — reset cached adapter instances."""
    _ADAPTER_CACHE.clear()
