"""Tests for NewsRiskFilterService."""

from datetime import datetime, timedelta, timezone

from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_risk_filter_service import NewsRiskFilterService
from agents.news.news_schemas import EventType, NewsEvent, NewsMode, NewsSource, SentimentLabel


def _event(**kwargs) -> NewsEvent:
    now = datetime.now(tz=timezone.utc)
    defaults = {
        "source": NewsSource.RSS,
        "headline": "Test headline",
        "published_at": now,
        "symbols_affected": ["MES"],
        "impact_score": 0.50,
        "urgency_score": 0.50,
        "volatility_score": 0.40,
        "sentiment_score": 0.30,
        "sentiment_label": SentimentLabel.BULLISH,
        "news_mode": NewsMode.CONTEXTUAL,
        "event_type": EventType.GENERAL_MARKET,
    }
    defaults.update(kwargs)
    return NewsEvent(**defaults)


def test_high_impact_news_blocks_trading():
    calendar = EconomicCalendarService()
    svc = NewsRiskFilterService(calendar)
    events = [
        _event(
            headline="CPI beats expectations",
            impact_score=0.95,
            news_mode=NewsMode.RISK_EVENT,
            event_type=EventType.CPI,
        )
    ]
    features = svc.compute_features("MES", events, technical_direction=1)
    assert features.high_impact_news_active is True
    assert features.trading_blocked is True
    assert "High-impact news" in features.news_risk_reason


def test_conflict_when_news_opposes_technical():
    calendar = EconomicCalendarService()
    svc = NewsRiskFilterService(calendar)
    events = [_event(sentiment_score=-0.80, impact_score=0.70)]
    features = svc.compute_features("MES", events, technical_direction=1)
    assert features.news_conflict_score > 0.5


def test_no_events_uses_calendar_only():
    calendar = EconomicCalendarService()
    calendar.add_breaking_event("Flash crash", symbol_override=["MES"])
    svc = NewsRiskFilterService(calendar)
    features = svc.compute_features("MES", [], technical_direction=0)
    assert features.affected_symbol_match is False
    assert features.trading_blocked is True


def test_volatility_triggers_size_reduction():
    calendar = EconomicCalendarService()
    svc = NewsRiskFilterService(calendar)
    events = [_event(volatility_score=0.70, impact_score=0.40)]
    features = svc.compute_features("MES", events)
    assert features.reduce_size_recommended is True
