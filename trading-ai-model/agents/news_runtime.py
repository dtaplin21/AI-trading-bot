"""Shared MarketNewsAgent instance for the application."""

from __future__ import annotations

import asyncio
import logging

from agents.news.market_news_agent import MarketNewsAgent
from config.settings import get_settings

logger = logging.getLogger(__name__)

_agent: MarketNewsAgent | None = None


def get_news_agent() -> MarketNewsAgent:
    global _agent
    if _agent is None:
        _agent = MarketNewsAgent()
    return _agent


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
    logger.info("MarketNewsAgent background loop started")
