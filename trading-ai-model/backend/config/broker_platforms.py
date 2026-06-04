"""Supported trading platforms (brokers / execution venues)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.settings import Settings


@dataclass(frozen=True)
class BrokerPlatform:
    id: str
    name: str
    category: str  # simulation | retail | futures | professional
    asset_classes: tuple[str, ...]
    website: str
    env_flag: str  # settings field that enables connection check
    detail_connected: str
    detail_disconnected: str


BROKER_PLATFORMS: tuple[BrokerPlatform, ...] = (
    BrokerPlatform(
        id="paper",
        name="Paper Trading",
        category="simulation",
        asset_classes=("futures", "stocks"),
        website="internal",
        env_flag="paper_trading_enabled",
        detail_connected="Simulated fills — no capital at risk",
        detail_disconnected="Enable PAPER_TRADING_ENABLED=true",
    ),
    BrokerPlatform(
        id="robinhood",
        name="Robinhood",
        category="retail",
        asset_classes=("stocks", "options", "crypto"),
        website="https://robinhood.com",
        env_flag="robinhood_access_token",
        detail_connected="Account linked — orders route to Robinhood",
        detail_disconnected="Set ROBINHOOD_ACCESS_TOKEN in .env to connect",
    ),
    BrokerPlatform(
        id="webull",
        name="Webull",
        category="retail",
        asset_classes=("stocks", "options"),
        website="https://www.webull.com",
        env_flag="webull_app_key",
        detail_connected="Account linked — orders route to Webull",
        detail_disconnected="Set WEBULL_APP_KEY and WEBULL_APP_SECRET in .env",
    ),
    BrokerPlatform(
        id="coinbase",
        name="Coinbase",
        category="retail",
        asset_classes=("crypto",),
        website="https://www.coinbase.com",
        env_flag="coinbase_api_key",
        detail_connected="Advanced Trade API — crypto spot (live gated)",
        detail_disconnected="Set COINBASE_API_KEY and COINBASE_API_SECRET; add coinbase to ENABLED_BROKERS",
    ),
    BrokerPlatform(
        id="oanda",
        name="OANDA",
        category="retail",
        asset_classes=("forex",),
        website="https://www.oanda.com",
        env_flag="oanda_api_key",
        detail_connected="v20 API — forex spot (practice or live)",
        detail_disconnected="Set OANDA_API_KEY (or ONDA_API_KEY) and OANDA_ACCOUNT_ID; add oanda to ENABLED_BROKERS",
    ),
    BrokerPlatform(
        id="alpaca",
        name="Alpaca",
        category="retail",
        asset_classes=("stocks", "options", "crypto"),
        website="https://alpaca.markets",
        env_flag="alpaca_api_key",
        detail_connected="API connected — stocks & options",
        detail_disconnected="Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env",
    ),
    BrokerPlatform(
        id="schwab",
        name="Charles Schwab",
        category="retail",
        asset_classes=("stocks", "options", "futures"),
        website="https://www.schwab.com",
        env_flag="schwab_app_key",
        detail_connected="OAuth linked — Schwab / thinkorswim API",
        detail_disconnected="Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET in .env",
    ),
    BrokerPlatform(
        id="tastytrade",
        name="tastytrade",
        category="retail",
        asset_classes=("stocks", "options", "futures"),
        website="https://tastytrade.com",
        env_flag="tastytrade_username",
        detail_connected="Session active — options & futures",
        detail_disconnected="Set TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD in .env",
    ),
    BrokerPlatform(
        id="ibkr",
        name="Interactive Brokers",
        category="professional",
        asset_classes=("futures", "stocks", "options", "forex"),
        website="https://www.interactivebrokers.com",
        env_flag="ibkr_account_id",
        detail_connected="TWS / Gateway connected — full futures access",
        detail_disconnected="Set IBKR_ACCOUNT_ID and run TWS Gateway on IBKR_PORT",
    ),
    BrokerPlatform(
        id="tradovate",
        name="Tradovate",
        category="futures",
        asset_classes=("futures",),
        website="https://tradovate.com",
        env_flag="tradovate_api_key",
        detail_connected="API connected — CME futures (MES, ES, NQ)",
        detail_disconnected="Set TRADOVATE_API_KEY and TRADOVATE_USERNAME in .env",
    ),
    BrokerPlatform(
        id="ninjatrader",
        name="NinjaTrader",
        category="futures",
        asset_classes=("futures",),
        website="https://ninjatrader.com",
        env_flag="ninjatrader_license_key",
        detail_connected="Continuum / Rithmic bridge active",
        detail_disconnected="Set NINJATRADER_LICENSE_KEY in .env",
    ),
)


def _is_enabled(settings: Settings, broker_id: str) -> bool:
    enabled = {b.strip().lower() for b in settings.enabled_brokers.split(",") if b.strip()}
    return broker_id.lower() in enabled


def _has_credentials(settings: Settings, broker: BrokerPlatform) -> bool:
    if broker.id == "paper":
        return settings.paper_trading_enabled
    if broker.id == "robinhood":
        return bool(settings.robinhood_access_token)
    if broker.id == "webull":
        return bool(settings.webull_app_key and settings.webull_app_secret)
    if broker.id == "coinbase":
        return bool(settings.coinbase_api_key and settings.coinbase_api_secret)
    if broker.id == "oanda":
        return bool(settings.oanda_api_key)
    if broker.id == "alpaca":
        return bool(settings.alpaca_api_key and settings.alpaca_secret_key)
    if broker.id == "schwab":
        return bool(settings.schwab_app_key and settings.schwab_app_secret)
    if broker.id == "tastytrade":
        return bool(settings.tastytrade_username and settings.tastytrade_password)
    if broker.id == "ibkr":
        return bool(settings.ibkr_account_id)
    if broker.id == "tradovate":
        return bool(settings.tradovate_api_key and settings.tradovate_username)
    if broker.id == "ninjatrader":
        return bool(settings.ninjatrader_license_key)
    return False


def resolve_broker_status(settings: Settings, broker: BrokerPlatform) -> str:
    """connected | configured | disconnected | disabled"""
    if broker.id == "paper":
        if not settings.paper_trading_enabled:
            return "disabled"
        return "connected"

    if not _has_credentials(settings, broker):
        return "disconnected"

    if _is_enabled(settings, broker.id):
        return "connected"

    return "configured"


def build_broker_platforms(settings: Optional[Settings] = None) -> list[dict]:
    settings = settings or __import__("config.settings", fromlist=["get_settings"]).get_settings()
    rows: list[dict] = []

    for broker in BROKER_PLATFORMS:
        status = resolve_broker_status(settings, broker)
        detail = broker.detail_connected if status == "connected" else broker.detail_disconnected
        if status == "configured":
            detail = f"Credentials saved — add '{broker.id}' to ENABLED_BROKERS to activate"

        account_hint = ""
        if broker.id == "ibkr" and settings.ibkr_account_id:
            account_hint = f" · Acct …{settings.ibkr_account_id[-4:]}" if len(settings.ibkr_account_id) >= 4 else ""
        elif broker.id == "coinbase" and settings.coinbase_api_key:
            mode = "Live" if settings.coinbase_live_enabled and not settings.paper_trading_enabled else "Paper / configured"
            account_hint = f" · {mode}"
        elif broker.id == "oanda" and settings.oanda_api_key:
            env_label = "Practice" if settings.oanda_practice else "Live"
            mode = (
                f"{env_label} trading"
                if settings.oanda_live_enabled and not settings.paper_trading_enabled
                else "Configured"
            )
            acct = settings.oanda_account_id
            acct_hint = f" · …{acct[-4:]}" if acct and len(acct) >= 4 else ""
            account_hint = f" · {mode}{acct_hint}"
        elif broker.id == "alpaca" and settings.alpaca_api_key:
            account_hint = " · " + ("Paper" if settings.alpaca_paper else "Live")

        rows.append(
            {
                "id": broker.id,
                "name": broker.name,
                "category": broker.category,
                "asset_classes": list(broker.asset_classes),
                "website": broker.website,
                "status": status,
                "detail": detail + account_hint,
                "enabled": _is_enabled(settings, broker.id) or (broker.id == "paper" and settings.paper_trading_enabled),
            }
        )

    return rows


def primary_execution_broker(settings: Optional[Settings] = None) -> str:
    """Broker id used for new orders (first enabled non-paper, else paper)."""
    settings = settings or __import__("config.settings", fromlist=["get_settings"]).get_settings()
    for broker in BROKER_PLATFORMS:
        if broker.id == "paper":
            continue
        if resolve_broker_status(settings, broker) == "connected":
            return broker.id
    return "paper" if settings.paper_trading_enabled else "none"
