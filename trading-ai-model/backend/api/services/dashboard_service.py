"""
api/services/dashboard_service.py

Builds the full dashboard payload for GET /dashboard.

Key change from previous version:
  build_watched_charts() calls watcher_charts_for_dashboard()
  from config/watchlist.py instead of parse_watchlist(settings.chart_watchlist).

The API and worker always show the same symbols when WATCHER_SYMBOLS is set.
CHART_WATCHLIST remains supported as an explicit override via env.

Data sources for chart rows (in priority order):
  1. TimescaleDB — live OHLCV bars written by ChartWatchRunner
  2. Session scheduler — open/closed + labels on each symbol
  3. Defaults — safe fallback (never crashes the dashboard)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional, cast

import pandas as pd

from agents.news_runtime import get_polling_status
from config.broker_platforms import build_broker_platforms, primary_execution_broker
from config.coinbase_symbols import is_coinbase_tradable
from config.execution_config import (
    coinbase_live_allowed,
    oanda_live_allowed,
    resolve_execution_mode,
)
from config.oanda_symbols import is_oanda_tradable
from risk.risk_runtime import get_risk_engine
from risk.order_sizing_runtime import get_order_sizing
from risk.kill_switch_runtime import get_kill_switch_status
from chart_watcher.watcher_runtime import (
    build_watcher_dashboard_summary,
    compute_feed_status,
    is_watcher_online,
    read_watcher_status,
    symbol_feed_stale_seconds,
)
from config.settings import get_settings
from config.watchlist import (
    WatchedChart,
    charts_grouped_by_asset_class,
    get_symbol_count,
    watcher_charts_for_dashboard,
    watcher_symbols_from_env,
)
from config.symbols import SYMBOL_MAP
from data.storage.pg_connect import is_database_url_placeholder
from data.storage.timescale_store import TimescaleStore
from paper_trading.position_book import get_position_book

logger = logging.getLogger(__name__)

_db_store: Optional[TimescaleStore] = None


def _database_url() -> str | None:
    url = (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()
    if not url or is_database_url_placeholder(url):
        return None
    return url


def _get_db() -> Optional[TimescaleStore]:
    global _db_store
    if _db_store is not None:
        return _db_store if _db_store.available else None
    try:
        url = _database_url()
        if url:
            _db_store = TimescaleStore(database_url=url)
        return _db_store if _db_store and _db_store.available else None
    except Exception as exc:
        logger.debug("dashboard_service: DB unavailable: %s", exc)
        return None


def _parse_bar_at(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _feed_status_with_db_fallback(
    chart: WatchedChart,
    *,
    watcher_online: bool,
    watcher_status: dict[str, Any] | None,
) -> str:
    """
    Worker heartbeat drives live feed status; fall back to DB bar age when worker is offline
    so seeded/historical bars do not show as fully Offline with no last bar.
    """
    status = compute_feed_status(
        watcher_online=watcher_online,
        symbol=chart.symbol.upper(),
        session_open=chart.session_open,
        watcher_status=watcher_status,
    )
    if status != "offline":
        return status

    last = _parse_bar_at(chart.last_bar_at)
    if last is None:
        return "offline"

    age = (datetime.now(timezone.utc) - last).total_seconds()
    if age <= symbol_feed_stale_seconds():
        return "stale"
    if not chart.session_open:
        return "session_closed"
    return "stale"


def _ohlcv_timeframes_for_chart(chart: WatchedChart) -> list[str]:
    """Prefer chart timeframe, then 1m (backfill often writes 1m only)."""
    candidates = [chart.timeframe, "1m", "5m", "15m", "1h"]
    seen: set[str] = set()
    ordered: list[str] = []
    for tf in candidates:
        if tf and tf not in seen:
            seen.add(tf)
            ordered.append(tf)
    return ordered


def build_watched_charts() -> list[WatchedChart]:
    """
    Watched chart list from WATCHER_SYMBOLS (same as worker).
    Enriches each entry with latest price + bar count from DB when available,
    then watcher heartbeat feed status when runtime_controls is populated.
    """
    charts = watcher_charts_for_dashboard(include_session_status=True)
    db = _get_db()
    if db:
        charts = _enrich_from_db(charts, db)
    charts = _enrich_from_watcher_runtime(charts)
    return charts


def _symbol_execution_ready(chart: WatchedChart, *, kill_switch: bool) -> bool:
    if kill_switch or chart.feed_status != "feeding":
        return False
    settings = get_settings()
    symbol = chart.symbol.upper()
    if settings.paper_trading_enabled:
        return True
    if oanda_live_allowed(settings) and is_oanda_tradable(symbol):
        return True
    if coinbase_live_allowed(settings) and is_coinbase_tradable(symbol):
        return True
    return False


def _enrich_from_watcher_runtime(charts: list[WatchedChart]) -> list[WatchedChart]:
    """Attach live worker heartbeat fields per symbol."""
    watcher_status = read_watcher_status()
    watcher_online = is_watcher_online(watcher_status)
    kill_switch = bool((watcher_status or {}).get("kill_switch"))
    if not kill_switch:
        kill_switch = get_kill_switch_status()["enabled"]
    bars_processed = (watcher_status or {}).get("bars_processed") or {}
    symbol_last_bar = (watcher_status or {}).get("symbol_last_bar") or {}

    for chart in charts:
        sym = chart.symbol.upper()
        chart.feed_status = _feed_status_with_db_fallback(
            chart,
            watcher_online=watcher_online,
            watcher_status=watcher_status,
        )
        chart.pipeline_running = watcher_online and chart.feed_status == "feeding"
        chart.watcher_bars_processed = int(bars_processed.get(sym) or bars_processed.get(chart.symbol) or 0)
        raw_last = symbol_last_bar.get(sym) or symbol_last_bar.get(chart.symbol)
        chart.watcher_last_bar_at = raw_last if isinstance(raw_last, str) else None
        chart.execution_ready = _symbol_execution_ready(chart, kill_switch=kill_switch)

    return charts


def _enrich_from_db(charts: list[WatchedChart], db: TimescaleStore) -> list[WatchedChart]:
    """Attach latest price and bar count from TimescaleDB."""
    for chart in charts:
        for tf in _ohlcv_timeframes_for_chart(chart):
            try:
                df = db.load_ohlcv(chart.symbol, tf, limit=1)
                if df is None or df.empty:
                    continue
                last_bar = cast(pd.Timestamp, df.index[-1]).to_pydatetime()
                if last_bar.tzinfo is None:
                    last_bar = last_bar.replace(tzinfo=timezone.utc)
                chart.last_price = float(df["close"].iloc[-1])
                chart.last_bar_at = last_bar.isoformat()
                chart.bar_count = db.count_bars(chart.symbol, tf)
                chart.is_active = True
                break
            except Exception as exc:
                logger.debug(
                    "dashboard_service: DB enrich failed for %s %s: %s",
                    chart.symbol,
                    tf,
                    exc,
                )
    return charts


def build_kill_switch_payload() -> dict[str, Any]:
    """Kill switch state for dashboard (matches GET /risk/kill-switch core fields)."""
    status = get_kill_switch_status()
    return {
        "enabled": status["enabled"],
        "env_default": status["env_default"],
        "updated_at": status.get("updated_at"),
    }


def build_system_status() -> dict[str, Any]:
    """System health summary for the dashboard status panel."""
    return {
        "paper_mode": os.getenv("PAPER_TRADING_ENABLED", "true"),
        "kill_switch": build_kill_switch_payload(),
        "watcher_mode": os.getenv("WATCHER_MODE", "paper"),
        "auto_promote": os.getenv("MODEL_AUTO_PROMOTE", "false"),
        "db_connected": _get_db() is not None,
        "database_url_configured": _database_url() is not None,
        "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "massive_key": bool(os.getenv("POLYGON_API_KEY")),
        "news_enabled": bool(
            os.getenv("NEWS_ENABLED", "true").lower() in ("true", "1", "yes")
        ),
    }


def build_session_summary(charts: list[WatchedChart]) -> dict[str, Any]:
    """How many symbols are currently in their trading session."""
    open_count = sum(1 for c in charts if c.session_open)
    closed_count = len(charts) - open_count
    by_class: dict[str, dict[str, int]] = {}
    for chart in charts:
        cls = chart.asset_class
        if cls not in by_class:
            by_class[cls] = {"open": 0, "closed": 0}
        if chart.session_open:
            by_class[cls]["open"] += 1
        else:
            by_class[cls]["closed"] += 1
    return {
        "total_open": open_count,
        "total_closed": closed_count,
        "by_class": by_class,
    }


def build_services() -> list[dict[str, Any]]:
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


def build_open_positions() -> list[dict[str, Any]]:
    return get_position_book().list_open()


def _build_core_dashboard(watched_objs: list[WatchedChart]) -> dict[str, Any]:
    settings = get_settings()
    platforms = build_broker_platforms(settings)
    connected = sum(1 for p in platforms if p["status"] == "connected")
    configured = sum(1 for p in platforms if p["status"] == "configured")
    active_broker = primary_execution_broker(settings)
    watched = [c.to_dict() for c in watched_objs]
    grouped = charts_grouped_by_asset_class(watched_objs)
    watcher_status_raw = read_watcher_status()
    watcher_summary = build_watcher_dashboard_summary(watched, watcher_status_raw)
    open_positions = build_open_positions()
    now = datetime.now(timezone.utc).isoformat()

    return {
        "updated_at": now,
        "timestamp": now,
        "execution_mode": resolve_execution_mode(settings),
        "coinbase_live_ready": coinbase_live_allowed(settings),
        "oanda_live_ready": oanda_live_allowed(settings),
        "order_sizing": get_order_sizing(),
        "risk_limits": get_risk_engine().risk_summary(),
        "active_broker": active_broker,
        "platforms": platforms,
        "platform_summary": {
            "connected": connected,
            "configured": configured,
            "total": len(platforms),
        },
        "services": build_services(),
        "open_positions": open_positions,
        "open_position_count": len(open_positions),
        "watched_charts": watched,
        "watched_chart_count": len(watched),
        "watched_charts_grouped": grouped,
        "charts_by_class": grouped,
        "watcher_symbol_summary": get_symbol_count(),
        "watcher_status": watcher_summary,
        "symbol_counts": get_symbol_count(),
        "total_symbols": len(watched),
        "system_status": build_system_status(),
        "session_summary": build_session_summary(watched_objs),
        "news_polling": get_polling_status(),
        "kill_switch": build_kill_switch_payload(),
        "source": "live",
    }


def build_dashboard() -> dict[str, Any]:
    """
    Full dashboard payload for GET /dashboard.
    Never raises — returns fallback with all WATCHER_SYMBOLS on failure.
    """
    try:
        return _build_core_dashboard(build_watched_charts())
    except Exception as exc:
        logger.error("dashboard_service: build_dashboard failed: %s", exc, exc_info=True)
        return _fallback_dashboard()


def _fallback_dashboard() -> dict[str, Any]:
    """Safe fallback — all watcher symbols with defaults, plus minimal broker UI."""
    symbols = watcher_symbols_from_env()
    primary_tf = os.getenv("WATCHLIST_PRIMARY_TF", "5m")
    watched_dicts: list[dict[str, Any]] = []

    for sym in symbols:
        spec = SYMBOL_MAP.get(sym)
        watched_dicts.append(
            {
                "symbol": sym,
                "timeframe": primary_tf,
                "display_name": spec.name if spec else sym,
                "label": spec.name if spec else sym,
                "asset_class": spec.asset_class if spec else "unknown",
                "session_type": spec.session if spec else "unknown",
                "session_open": False,
                "session_label": "",
                "last_price": None,
                "last_bar_at": None,
                "bar_count": 0,
                "is_active": False,
                "status": "offline",
                "feed_status": "offline",
                "pipeline_running": False,
                "execution_ready": False,
                "watcher_bars_processed": 0,
                "watcher_last_bar_at": None,
                "pipeline_active": False,
                "tick_value": spec.tick_value if spec else 1.0,
                "massive_api_symbol": sym,
            }
        )

    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "updated_at": now,
        "timestamp": now,
        "watched_charts": watched_dicts,
        "watched_chart_count": len(watched_dicts),
        "watched_charts_grouped": {},
        "charts_by_class": {},
        "watcher_symbol_summary": get_symbol_count(),
        "symbol_counts": get_symbol_count(),
        "total_symbols": len(symbols),
        "system_status": build_system_status(),
        "session_summary": {"total_open": 0, "total_closed": len(symbols), "by_class": {}},
        "watcher_status": build_watcher_dashboard_summary(watched_dicts, None),
        "open_positions": [],
        "open_position_count": 0,
        "platforms": [],
        "platform_summary": {"connected": 0, "configured": 0, "total": 0},
        "services": [],
        "news_polling": {"enabled": False, "running": False},
        "kill_switch": build_kill_switch_payload(),
        "source": "fallback",
        "execution_mode": "paper",
        "active_broker": "paper",
    }

    try:
        settings = get_settings()
        payload["platforms"] = build_broker_platforms(settings)
        payload["platform_summary"] = {
            "connected": sum(1 for p in payload["platforms"] if p["status"] == "connected"),
            "configured": sum(1 for p in payload["platforms"] if p["status"] == "configured"),
            "total": len(payload["platforms"]),
        }
        payload["active_broker"] = primary_execution_broker(settings)
        payload["open_positions"] = build_open_positions()
        payload["open_position_count"] = len(payload["open_positions"])
        payload["news_polling"] = get_polling_status()
        payload["services"] = build_services()
    except Exception as exc:
        logger.debug("dashboard_service: fallback partial enrich failed: %s", exc)

    return payload
