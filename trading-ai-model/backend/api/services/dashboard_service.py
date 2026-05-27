"""Aggregates broker platforms, open positions, and watched charts for the UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.news_runtime import get_news_agent
from config.broker_platforms import build_broker_platforms, primary_execution_broker
from config.settings import get_settings
from config.watchlist import DEFAULT_WATCHLIST, parse_watchlist
from data.storage.timescale_store import TimescaleStore
from paper_trading.position_book import get_position_book


def build_services() -> list[dict[str, Any]]:
    """Internal infrastructure (not broker platforms) — optional detail for ops."""
    settings = get_settings()
    store = TimescaleStore()
    news_status = get_news_agent().get_status()

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
            "status": "connected" if news_status.get("running") else "disconnected",
            "detail": f"{news_status.get('cached_events', 0)} events cached",
        },
        {
            "id": "lightgbm",
            "name": "LightGBM Model",
            "status": "configured",
            "detail": settings.production_model_id,
        },
    ]


def build_watched_charts() -> list[dict[str, Any]]:
    settings = get_settings()
    store = TimescaleStore()
    charts = parse_watchlist(settings.chart_watchlist)

    rows: list[dict[str, Any]] = []
    for chart in charts:
        last_bar: datetime | None = None
        last_price: float | None = None

        if store.available:
            try:
                df = store.load_ohlcv(chart.symbol, chart.timeframe, limit=1)
                if df is not None and not df.empty:
                    last_bar = df.index[-1].to_pydatetime()
                    if last_bar.tzinfo is None:
                        last_bar = last_bar.replace(tzinfo=timezone.utc)
                    last_price = float(df["close"].iloc[-1])
            except Exception:
                pass

        rows.append(
            {
                "symbol": chart.symbol,
                "timeframe": chart.timeframe,
                "label": chart.label or chart.symbol,
                "status": "live" if last_bar else "watching",
                "last_bar_at": last_bar.isoformat() if last_bar else None,
                "last_price": last_price,
                "pipeline_active": True,
            }
        )

    if not rows:
        for chart in DEFAULT_WATCHLIST:
            rows.append(
                {
                    "symbol": chart.symbol,
                    "timeframe": chart.timeframe,
                    "label": chart.label,
                    "status": "watching",
                    "last_bar_at": None,
                    "last_price": None,
                    "pipeline_active": True,
                }
            )

    return rows


def build_open_positions() -> list[dict[str, Any]]:
    return get_position_book().list_open()


def build_dashboard() -> dict[str, Any]:
    settings = get_settings()
    platforms = build_broker_platforms(settings)
    connected = sum(1 for p in platforms if p["status"] == "connected")
    configured = sum(1 for p in platforms if p["status"] == "configured")
    active_broker = primary_execution_broker(settings)

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "paper" if settings.paper_trading_enabled and active_broker == "paper" else "live",
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
        "watched_charts": build_watched_charts(),
        "watched_chart_count": len(build_watched_charts()),
    }
