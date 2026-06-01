"""Async DB adapter for news events."""

from __future__ import annotations

import asyncio
import logging

from typing import Optional

from agents.news.news_schemas import EconomicEvent, NewsEvent, NewsFeatures, NewsRiskWindow, SymbolNewsImpact
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

    async def insert_economic_event(self, event: EconomicEvent) -> None:
        await asyncio.to_thread(self._store.insert_economic_event, event)

    async def insert_risk_windows(self, windows: list[NewsRiskWindow]) -> None:
        await asyncio.to_thread(self._store.insert_risk_windows, windows)

    async def insert_news_feature_snapshot(
        self,
        features: NewsFeatures,
        symbol: str,
        timeframe: str,
        signal_id: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._store.insert_news_feature_snapshot,
            features,
            symbol,
            timeframe,
            signal_id,
        )

    async def fetch_active_risk_windows(self, symbol: str):
        return await asyncio.to_thread(self._store.fetch_active_risk_windows, symbol)

    async def fetch_upcoming_economic_events(self, hours_ahead: int = 48):
        return await asyncio.to_thread(self._store.fetch_upcoming_economic_events, hours_ahead)

    async def fetch_recent_news_events(
        self,
        hours: int = 6,
        symbol: Optional[str] = None,
        limit: int = 500,
    ) -> list[NewsEvent]:
        return await asyncio.to_thread(
            self._store.fetch_recent_news_events,
            hours,
            symbol,
            limit,
        )

    @property
    def store(self) -> TimescaleStore:
        return self._store

    @property
    def available(self) -> bool:
        return self._store.available
