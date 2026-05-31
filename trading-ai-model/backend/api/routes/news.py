"""News intelligence API routes."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from agents.news.news_schemas import EconomicEvent, EventType, ImpactLevel, NewsSource
from agents.news_runtime import get_news_agent

router = APIRouter()


@router.get("/status")
def news_status():
    return get_news_agent().get_status()


@router.post("/refresh")
async def refresh_news():
    events = await get_news_agent().run_once()
    return {"ingested": len(events), "status": get_news_agent().get_status()}


@router.get("/features/{symbol}")
def news_features(symbol: str, technical_direction: int = Query(0)):
    nf = get_news_agent().get_news_features(symbol.upper(), technical_direction)
    return nf.model_dump()


@router.get("/events")
def recent_events(symbol: str | None = None, hours: int = 24):
    events = get_news_agent().get_recent_events(symbol=symbol, hours=hours)
    return {"events": [e.model_dump() for e in events]}


@router.get("/calendar/schedule")
def calendar_schedule_status():
    agent = get_news_agent()
    return {
        "status": agent.get_status().get("calendar_scheduler"),
        "upcoming": [e.model_dump() for e in agent.get_upcoming_events(hours=168)],
    }


@router.post("/calendar/sync")
async def sync_calendar():
    from agents.news.calendar.calendar_sync import CalendarSyncService

    svc = CalendarSyncService()
    result = await svc.sync(get_news_agent()._calendar)
    return result


@router.get("/calendar")
def upcoming_calendar(hours: int = 24):
    events = get_news_agent().get_upcoming_events(hours)
    return {"events": [e.model_dump() for e in events]}


@router.post("/calendar")
def add_calendar_event(
    name: str,
    symbol: str = "MES",
    hours_from_now: float = 2,
    impact: ImpactLevel = ImpactLevel.HIGH,
    event_type: EventType = EventType.GENERAL_MARKET,
):
    agent = get_news_agent()
    event = EconomicEvent(
        event_name=name,
        event_type=event_type,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=hours_from_now),
        impact_level=impact,
        affected_symbols=[symbol.upper()],
        source=NewsSource.FRED,
    )
    agent.add_economic_event(event)
    return {"added": event.model_dump()}
