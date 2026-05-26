"""Tests for Market News Intelligence Agent."""

import asyncio

import pytest

from agents.news.market_news_agent import MarketNewsAgent
from agents.news.news_schemas import EconomicEvent, EventType, ImpactLevel


@pytest.fixture
def news_agent():
    return MarketNewsAgent(use_llm=False, polling_interval=3600)


@pytest.mark.asyncio
async def test_run_once_ingests_events(news_agent):
    events = await news_agent.run_once()
    assert len(events) >= 1
    assert events[0].headline
    assert events[0].source.value == "rss"


@pytest.mark.asyncio
async def test_get_news_features(news_agent):
    await news_agent.run_once()
    features = news_agent.get_news_features("MES", technical_direction=1)
    assert features.affected_symbol_match or features.news_impact_score >= 0
    assert features.latest_headline is not None or features.news_risk_reason


def test_trading_blocked_on_breaking_escalation(news_agent):
    asyncio.run(news_agent.run_once())
    news_agent._calendar.add_breaking_event("Test crash", symbol_override=["MES"])
    blocked, reason = news_agent.is_trading_blocked("MES")
    assert blocked is True
    assert "Breaking" in reason


def test_economic_calendar_blackout(news_agent):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    news_agent.add_economic_event(
        EconomicEvent(
            event_name="FOMC",
            event_type=EventType.FOMC,
            scheduled_at=now + timedelta(minutes=5),
            impact_level=ImpactLevel.HIGH,
            affected_symbols=["MES"],
        )
    )
    blocked, _ = news_agent.is_trading_blocked("MES")
    assert blocked is True


def test_size_reduction(news_agent):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    news_agent.add_economic_event(
        EconomicEvent(
            event_name="PMI",
            event_type=EventType.GENERAL_MARKET,
            scheduled_at=now + timedelta(minutes=20),
            impact_level=ImpactLevel.MEDIUM,
            affected_symbols=["ES"],
        )
    )
    assert news_agent.get_size_reduction_factor("ES") <= 0.75


def test_get_latest_explanation(news_agent):
    asyncio.run(news_agent.run_once())
    text = news_agent.get_latest_explanation("MES")
    assert "news" in text.lower() or "Fed" in text or "S&P" in text or "recent" in text.lower()
