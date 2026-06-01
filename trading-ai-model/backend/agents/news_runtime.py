"""Shared MarketNewsAgent instance for the application."""

from __future__ import annotations

import asyncio
import logging

from agents.news.db_news_reader import DbNewsReader
from agents.news.market_news_agent import MarketNewsAgent
from config.settings import get_settings

logger = logging.getLogger(__name__)

_agent: MarketNewsAgent | None = None
_reader: DbNewsReader | None = None
_polling_override: bool | None = None


def get_news_agent() -> MarketNewsAgent:
    global _agent
    if _agent is None:
        _agent = MarketNewsAgent()
    return _agent


def get_news_reader() -> DbNewsReader:
    """Read-only news for watcher — no ingestion polling."""
    global _reader
    if _reader is None:
        _reader = DbNewsReader()
    return _reader


def get_watcher_news():
    """
    News handle for ChartWatchRunner.
    Default: DbNewsReader (API process ingests).
    Set WATCHER_NEWS_SOURCE=local for standalone watch without API.
    """
    settings = get_settings()
    if settings.watcher_news_source.lower() == "local":
        logger.info("Watcher news: local MarketNewsAgent (standalone ingestion)")
        return get_news_agent()
    return get_news_reader()


def is_news_polling_enabled() -> bool:
    if _polling_override is not None:
        return _polling_override
    return get_settings().news_enabled


def get_polling_status() -> dict:
    agent = get_news_agent()
    status = agent.get_status()
    return {
        "enabled": is_news_polling_enabled(),
        "running": bool(status.get("running")),
        "env_default": get_settings().news_enabled,
        "last_run": status.get("last_run"),
        "cached_events": status.get("cached_events", 0),
        "polling_interval_seconds": status.get("polling_interval"),
        "calendar_scheduler": status.get("calendar_scheduler"),
    }


async def set_news_polling_enabled(enabled: bool) -> dict:
    """Start or stop automatic news ingestion (baseline + calendar triggers)."""
    global _polling_override
    _polling_override = enabled
    agent = get_news_agent()

    if enabled:
        if not agent._running:
            agent.start_background()
            logger.info("News polling enabled via runtime switch")
    else:
        if agent._running:
            await agent.stop()
            agent._task = None
            logger.info("News polling disabled via runtime switch — no API/LLM ingest")

    return get_polling_status()


def bootstrap_news_sync() -> None:
    """Load initial news cache when no background loop is running."""
    if not is_news_polling_enabled():
        return
    agent = get_news_agent()
    if agent._last_run:
        return
    try:
        asyncio.get_running_loop()
        logger.debug("Event loop active — skip sync bootstrap")
    except RuntimeError:
        asyncio.run(agent.run_once())


async def start_news_background() -> None:
    if not is_news_polling_enabled():
        logger.info("News polling disabled — skipping background ingest on startup")
        return
    agent = get_news_agent()
    await agent.run_once()
    agent.start_background()
    logger.info("MarketNewsAgent background loop started (API owns ingestion)")
