"""
live/market_data_router.py

Unified market-data routing — mirrors execution routing in broker_router.py.

Primary order from MARKET_DATA_PRIMARY (default: coinbase,oanda,polygon):
  crypto → Coinbase when listed first and creds ready; Polygon demoted when
           coinbase precedes polygon (avoids zero-close poison on crypto).
  forex  → OANDA when listed first and creds ready; Polygon demoted likewise.
  futures/equities → Polygon when key present.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from config.coinbase_symbols import is_coinbase_tradable
from config.execution_config import coinbase_credentials_ready, oanda_credentials_ready
from config.market_data_config import (
    parse_market_data_primary,
    polygon_demoted_for_crypto,
    polygon_demoted_for_forex,
)
from config.oanda_symbols import is_oanda_tradable
from config.settings import Settings, get_settings

if TYPE_CHECKING:
    from live.broker_adapter import BrokerAdapter

logger = logging.getLogger(__name__)

_ADAPTER_CACHE: dict[str, BrokerAdapter] = {}


def _primary_for_settings(settings: Settings | None) -> tuple[str, ...]:
    if settings is not None and getattr(settings, "market_data_primary", "").strip():
        return parse_market_data_primary(settings.market_data_primary)
    return parse_market_data_primary()


def _polygon_api_key_ready(settings: Settings, *, env_fallback: bool = True) -> bool:
    key = (settings.polygon_api_key or "").strip()
    if key:
        return True
    if env_fallback:
        return bool(os.getenv("POLYGON_API_KEY", "").strip())
    return False


def resolve_market_data_broker_id(
    symbol: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Return market-data broker id for a symbol (coinbase | oanda | polygon | none)."""
    explicit_settings = settings is not None
    settings = settings or get_settings()
    sym = symbol.upper()
    primary = _primary_for_settings(settings)
    coinbase_eligible = is_coinbase_tradable(sym)
    oanda_eligible = is_oanda_tradable(sym)
    demote_crypto = coinbase_eligible and polygon_demoted_for_crypto(primary)
    demote_forex = oanda_eligible and polygon_demoted_for_forex(primary)
    polygon_ready = _polygon_api_key_ready(
        settings,
        env_fallback=not explicit_settings,
    )

    for broker in primary:
        if broker == "coinbase":
            if coinbase_eligible and coinbase_credentials_ready(settings):
                return "coinbase"
        elif broker == "oanda":
            if oanda_eligible and oanda_credentials_ready(settings):
                return "oanda"
        elif broker == "polygon":
            if not polygon_ready:
                continue
            if coinbase_eligible and demote_crypto:
                continue
            if oanda_eligible and demote_forex:
                continue
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


def build_market_data_feed_summary(
    settings: Settings | None = None,
) -> dict[str, object]:
    """
    Dashboard-facing summary of configured market-data feeds by asset class.

    Example label: "Crypto: Coinbase | Forex: OANDA | Futures: Polygon"
    """
    settings = settings or get_settings()
    primary = _primary_for_settings(settings)
    samples = {
        "crypto": "BTCUSD",
        "forex": "EURUSD",
        "futures": "MES",
        "equities": "TSLA",
    }
    by_class = {
        asset: resolve_market_data_broker_id(symbol, settings=settings)
        for asset, symbol in samples.items()
    }
    labels = {
        "coinbase": "Coinbase",
        "oanda": "OANDA",
        "polygon": "Polygon",
        "none": "None",
    }
    parts = [
        f"{asset.title()}: {labels.get(broker, broker)}"
        for asset, broker in by_class.items()
        if broker != "none" or asset in ("crypto", "forex", "futures")
    ]
    return {
        "primary": list(primary),
        "tick_stream_mode": os.getenv("TICK_STREAM_MODE", "broker").strip().lower(),
        "by_asset_class": by_class,
        "label": " | ".join(parts) if parts else "No market-data feeds configured",
    }

