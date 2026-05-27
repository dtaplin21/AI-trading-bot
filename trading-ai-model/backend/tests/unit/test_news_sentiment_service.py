"""Tests for NewsSentimentService rule engine."""

from datetime import datetime, timezone

import pytest

from agents.news.news_schemas import EventType, NewsMode, NewsSource, RawNewsItem, SentimentLabel
from agents.news.news_sentiment_service import NewsSentimentService


def _item(headline: str, summary: str = "") -> RawNewsItem:
    return RawNewsItem(
        source=NewsSource.RSS,
        headline=headline,
        summary=summary or None,
        published_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def svc():
    return NewsSentimentService(use_llm=False)


@pytest.mark.asyncio
async def test_classify_cpi_high_impact(svc):
    event = await svc.classify(_item("US CPI release beats expectations", "Core inflation rises"))
    assert event.event_type == EventType.CPI
    assert event.impact_score >= 0.85
    assert event.news_mode == NewsMode.RISK_EVENT
    assert event.sentiment_label == SentimentLabel.BULLISH


@pytest.mark.asyncio
async def test_classify_bearish_earnings(svc):
    event = await svc.classify(_item("Company misses earnings, stock plunges"))
    assert event.event_type == EventType.EARNINGS
    assert event.sentiment_score < -0.5
    assert event.sentiment_label == SentimentLabel.BEARISH


@pytest.mark.asyncio
async def test_classify_general_market_low_impact(svc):
    event = await svc.classify(_item("Markets open for regular session"))
    assert event.event_type == EventType.GENERAL_MARKET
    assert event.impact_score <= 0.35
    assert event.news_mode == NewsMode.INFORMATIONAL
    assert event.trade_action == "none"


@pytest.mark.asyncio
async def test_classify_fomc_risk_event(svc):
    event = await svc.classify(_item("Breaking: emergency FOMC rate decision announced"))
    assert event.event_type == EventType.FOMC
    assert event.news_mode == NewsMode.RISK_EVENT
    assert event.trade_action in ("block", "risk_filter", "reduce_size")


@pytest.mark.asyncio
async def test_classify_batch(svc):
    items = [
        _item("FOMC holds rates steady"),
        _item("Oil inventory data released"),
    ]
    events = await svc.classify_batch(items)
    assert len(events) == 2
    assert events[0].event_type == EventType.FOMC
    assert events[1].event_type == EventType.OIL_INVENTORY
