"""Aggregates broker platforms, open positions, and watched charts for the UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.news_runtime import get_news_agent, get_polling_status
from config.broker_platforms import build_broker_platforms, primary_execution_broker
from config.settings import get_settings
from config.watchlist import (
    charts_grouped_by_asset_class,
    get_symbol_count,
    watcher_charts_for_dashboard,
)
from data.storage.timescale_store import TimescaleStore
from paper_trading.position_book import get_position_book


def build_services() -> list[dict[str, Any]]:
    """Internal infrastructure (not broker platforms) — optional detail for ops."""
    settings = get_settings()
    store = TimescaleStore()
    polling = get_polling_status()

    return [
        {
            "id": "timescaledb",
            "name": "TimescaleDB",
            "status": "connected" if store.available else "disconnected",
            "detail": "Market data & observations" if store.available else "Set DATABASE_URL",
        },
        {
            "id": "news_agent",
            "name": "News Intelligence",
            "status": "connected" if polling.get("running") else "disabled",
            "detail": (
                f"Polling on · {polling.get('cached_events', 0)} cached"
                if polling.get("enabled") and polling.get("running")
                else f"Polling off · {polling.get('cached_events', 0)} cached"
            ),
        },
        {
            "id": "lightgbm",
            "name": "LightGBM Model",
            "status": "configured",
            "detail": settings.production_model_id,
        },
    ]


def build_watched_charts() -> list[dict[str, Any]]:
    store = TimescaleStore()
    charts = watcher_charts_for_dashboard(include_session_status=True)

    rows: list[dict[str, Any]] = []
    for chart in charts:
        if store.available:
            try:
                df = store.load_ohlcv(chart.symbol, chart.timeframe, limit=1)
                if df is not None and not df.empty:
                    last_bar = df.index[-1].to_pydatetime()
                    if last_bar.tzinfo is None:
                        last_bar = last_bar.replace(tzinfo=timezone.utc)
                    chart.last_bar_at = last_bar.isoformat()
                    chart.last_price = float(df["close"].iloc[-1])
                    chart.bar_count = max(chart.bar_count, 1)
            except Exception:
                pass

        rows.append(chart.to_dict())

    return rows


def build_open_positions() -> list[dict[str, Any]]:
    return get_position_book().list_open()


def build_dashboard() -> dict[str, Any]:
    settings = get_settings()
    platforms = build_broker_platforms(settings)
    connected = sum(1 for p in platforms if p["status"] == "connected")
    configured = sum(1 for p in platforms if p["status"] == "configured")
    active_broker = primary_execution_broker(settings)
    watched = build_watched_charts()

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "paper"
        if settings.paper_trading_enabled and active_broker == "paper"
        else "live",
        "active_broker": active_broker,
        "platforms": platforms,
        "platform_summary": {
            "connected": connected,
            "configured": configured,
            "total": len(platforms),
        },
        "services": build_services(),
        "open_positions": build_open_positions(),
        "open_position_count": len(build_open_positions()),
        "watched_charts": watched,
        "watched_chart_count": len(watched),
        "watched_charts_grouped": charts_grouped_by_asset_class(),
        "watcher_symbol_summary": get_symbol_count(),
        "news_polling": get_polling_status(),
    }
