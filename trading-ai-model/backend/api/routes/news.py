"""News intelligence API routes."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from agents.news.news_schemas import EconomicEvent, EventType, ImpactLevel, NewsSource
from agents.news_runtime import (
    get_news_agent,
    get_polling_status,
    is_news_polling_enabled,
    set_news_polling_enabled,
)

router = APIRouter()


class NewsPollingUpdate(BaseModel):
    enabled: bool


@router.get("/polling")
def news_polling_status():
    """Runtime switch for automatic news ingest (baseline + calendar triggers)."""
    return get_polling_status()


@router.put("/polling")
async def update_news_polling(body: NewsPollingUpdate):
    return await set_news_polling_enabled(body.enabled)


@router.get("/status")
def news_status():
    status = get_news_agent().get_status()
    status["polling_enabled"] = is_news_polling_enabled()
    return status


@router.post("/refresh")
async def refresh_news():
    """Manual one-shot ingest (works even when automatic polling is off)."""
    events = await get_news_agent().run_once()
    status = get_news_agent().get_status()
    status["polling_enabled"] = is_news_polling_enabled()
    return {"ingested": len(events), "status": status}


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
        "upcoming": [e.model_dump() for e in agent.get_upcoming_events(hours_ahead=168)],
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
