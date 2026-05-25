"""Async DB adapter for news events."""

from __future__ import annotations

import asyncio
import logging

from agents.news.news_schemas import NewsEvent, SymbolNewsImpact
from data.storage.timescale_store import TimescaleStore

logger = logging.getLogger(__name__)


class NewsDbStore:
    """Async wrapper around TimescaleStore news methods."""

    def __init__(self, store: TimescaleStore | None = None):
        self._store = store or TimescaleStore()

    async def insert_news_events(self, events: list[NewsEvent]) -> None:
        await asyncio.to_thread(self._store.insert_news_events, events)

    async def insert_symbol_impacts(self, impacts: list[SymbolNewsImpact]) -> None:
        await asyncio.to_thread(self._store.insert_symbol_impacts, impacts)

    @property
    def available(self) -> bool:
        return self._store.available
