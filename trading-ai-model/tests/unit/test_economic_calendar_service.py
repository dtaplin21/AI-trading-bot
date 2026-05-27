"""Tests for EconomicCalendarService."""

from datetime import datetime, timedelta, timezone

from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_schemas import EconomicEvent, EventType, ImpactLevel


def _svc() -> EconomicCalendarService:
    return EconomicCalendarService()


def test_fomc_pre_window_blocks_trading():
    svc = _svc()
    now = datetime.now(tz=timezone.utc)
    svc.add_event(
        EconomicEvent(
            event_name="FOMC",
            event_type=EventType.FOMC,
            scheduled_at=now + timedelta(minutes=10),
            impact_level=ImpactLevel.HIGH,
            affected_symbols=["MES"],
        )
    )
    blocked, reason = svc.is_trading_blocked("MES", at=now)
    assert blocked is True
    assert "FOMC" in reason


def test_jobless_claims_allows_trading_with_size_reduction():
    svc = _svc()
    now = datetime.now(tz=timezone.utc)
    svc.add_event(
        EconomicEvent(
            event_name="Jobless Claims",
            event_type=EventType.JOBLESS_CLAIMS,
            scheduled_at=now + timedelta(minutes=5),
            impact_level=ImpactLevel.MEDIUM,
            affected_symbols=["ES"],
        )
    )
    blocked, _ = svc.is_trading_blocked("ES", at=now)
    assert blocked is False
    assert svc.get_size_reduction_factor("ES", at=now) == 0.5


def test_breaking_event_blocks_immediately():
    svc = _svc()
    window = svc.add_breaking_event("Flash crash", symbol_override=["MES"])
    assert window.event_type == EventType.BREAKING
    blocked, reason = svc.is_trading_blocked("MES")
    assert blocked is True
    assert "Breaking" in reason


def test_minutes_until_next_high_impact_event():
    svc = _svc()
    now = datetime.now(tz=timezone.utc)
    svc.add_event(
        EconomicEvent(
            event_name="CPI",
            event_type=EventType.CPI,
            scheduled_at=now + timedelta(minutes=30),
            impact_level=ImpactLevel.HIGH,
            affected_symbols=["ES"],
        )
    )
    minutes = svc.minutes_until_next_event("ES")
    assert 29 <= minutes <= 31
