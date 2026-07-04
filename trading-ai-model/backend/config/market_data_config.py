"""Market-data routing preferences (MARKET_DATA_PRIMARY)."""

from __future__ import annotations

import os

DEFAULT_MARKET_DATA_PRIMARY = "coinbase,oanda,polygon"


def parse_market_data_primary(raw: str | None = None) -> tuple[str, ...]:
    """Parse comma-separated broker ids (coinbase, oanda, polygon)."""
    value = (raw if raw is not None else os.getenv("MARKET_DATA_PRIMARY", "")).strip()
    if not value:
        value = DEFAULT_MARKET_DATA_PRIMARY
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())


def polygon_demoted_for_crypto(primary: tuple[str, ...] | None = None) -> bool:
    """When True, Polygon must not subscribe to crypto symbols."""
    order = primary if primary is not None else parse_market_data_primary()
    if "coinbase" not in order:
        return False
    if "polygon" not in order:
        return True
    return order.index("coinbase") < order.index("polygon")


def polygon_demoted_for_forex(primary: tuple[str, ...] | None = None) -> bool:
    """When True, Polygon must not subscribe to forex symbols."""
    order = primary if primary is not None else parse_market_data_primary()
    if "oanda" not in order:
        return False
    if "polygon" not in order:
        return True
    return order.index("oanda") < order.index("polygon")
