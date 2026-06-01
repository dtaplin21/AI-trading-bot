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


def bootstrap_news_sync() -> None:
    """Load initial news cache when no background loop is running."""
    agent = get_news_agent()
    if agent._last_run:
        return
    try:
        asyncio.get_running_loop()
        logger.debug("Event loop active — skip sync bootstrap")
    except RuntimeError:
        asyncio.run(agent.run_once())


async def start_news_background() -> None:
    settings = get_settings()
    if not settings.news_enabled:
        return
    agent = get_news_agent()
    await agent.run_once()
    agent.start_background()
    logger.info("MarketNewsAgent background loop started (API owns ingestion)")
