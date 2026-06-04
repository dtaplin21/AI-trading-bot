"""Resolve execution mode: paper (default) vs Coinbase / OANDA live."""

from __future__ import annotations

from config.settings import Settings, get_settings


def coinbase_credentials_ready(settings: Settings) -> bool:
    return bool(settings.coinbase_api_key.strip() and settings.coinbase_api_secret.strip())


def oanda_credentials_ready(settings: Settings) -> bool:
    return bool(settings.oanda_api_key.strip())


def coinbase_live_allowed(settings: Settings | None = None) -> bool:
    """
    Live Coinbase orders require ALL of:
      - PAPER_TRADING_ENABLED=false
      - COINBASE_LIVE_ENABLED=true
      - API credentials present
      - 'coinbase' in ENABLED_BROKERS
    """
    settings = settings or get_settings()
    enabled = {b.strip().lower() for b in settings.enabled_brokers.split(",") if b.strip()}
    return (
        not settings.paper_trading_enabled
        and settings.coinbase_live_enabled
        and coinbase_credentials_ready(settings)
        and "coinbase" in enabled
    )


def oanda_live_allowed(settings: Settings | None = None) -> bool:
    """
    Live OANDA orders require ALL of:
      - PAPER_TRADING_ENABLED=false
      - OANDA_LIVE_ENABLED=true
      - API key present
      - 'oanda' in ENABLED_BROKERS
    """
    settings = settings or get_settings()
    enabled = {b.strip().lower() for b in settings.enabled_brokers.split(",") if b.strip()}
    return (
        not settings.paper_trading_enabled
        and settings.oanda_live_enabled
        and oanda_credentials_ready(settings)
        and "oanda" in enabled
    )


def resolve_execution_mode(settings: Settings | None = None) -> str:
    """Returns 'paper', 'coinbase', 'oanda', 'live' (both), or 'disabled'."""
    settings = settings or get_settings()
    if settings.paper_trading_enabled:
        return "paper"
    cb = coinbase_live_allowed(settings)
    oa = oanda_live_allowed(settings)
    if cb and oa:
        return "live"
    if cb:
        return "coinbase"
    if oa:
        return "oanda"
    return "disabled"
