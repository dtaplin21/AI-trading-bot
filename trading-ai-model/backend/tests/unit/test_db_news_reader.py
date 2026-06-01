"""Tests for DbNewsReader — watcher-side DB news without ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.news.db_news_reader import DbNewsReader
from agents.news.news_schemas import EventType, NewsEvent, NewsMode, NewsSource, SentimentLabel


@pytest.fixture
def mock_db_store():
    store = MagicMock()
    store.available = True
    store.store = MagicMock(available=True)
    store.fetch_recent_news_events = AsyncMock(return_value=[])
    return store


@pytest.mark.asyncio
async def test_refresh_loads_events_from_db(mock_db_store):
    event = NewsEvent(
        id="e1",
        source=NewsSource.RSS,
        headline="CPI hotter than expected",
        published_at=datetime.now(timezone.utc),
        event_type=EventType.CPI,
        news_mode=NewsMode.RISK_EVENT,
        symbols_affected=["MES", "ES"],
        impact_score=0.85,
        sentiment_label=SentimentLabel.BEARISH,
    )
    mock_db_store.fetch_recent_news_events = AsyncMock(return_value=[event])

    reader = DbNewsReader(db_store=mock_db_store, refresh_seconds=3600)
    await reader.refresh()

    assert len(reader._events) == 1
    features = reader.get_news_features("MES", technical_direction=1)
    assert features.news_impact_score >= 0


def test_get_watcher_news_defaults_to_db_reader():
    from agents.news.db_news_reader import DbNewsReader
    from agents.news_runtime import get_watcher_news

    news = get_watcher_news()
    assert isinstance(news, DbNewsReader)
