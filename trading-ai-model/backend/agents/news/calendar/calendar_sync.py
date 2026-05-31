"""Sync calendar providers into persistent schedule + economic calendar service."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agents.news.calendar.calendar_store import CalendarScheduleStore
from agents.news.calendar.classifier import enrich_draft, should_schedule_polling
from agents.news.calendar.registry import CalendarProviderRegistry, get_calendar_registry
from agents.news.calendar.schemas import CalendarEventDraft
from agents.news.news_schemas import EconomicEvent, NewsSource

if TYPE_CHECKING:
    from agents.news.economic_calendar_service import EconomicCalendarService

logger = logging.getLogger(__name__)


class CalendarSyncService:
    def __init__(
        self,
        store: CalendarScheduleStore | None = None,
        registry: CalendarProviderRegistry | None = None,
        days_ahead: int = 14,
        trigger_offsets_minutes: list[int] | None = None,
    ) -> None:
        self._store = store or CalendarScheduleStore()
        self._registry = registry or get_calendar_registry()
        self._days_ahead = days_ahead
        self._offsets = trigger_offsets_minutes or [-15, 0, 5]

    async def sync(
        self,
        economic_calendar: EconomicCalendarService | None = None,
    ) -> dict:
        drafts = await self._registry.fetch_all(self._days_ahead)
        scheduled = 0
        triggers = 0
        registered = 0

        for raw in drafts:
            draft = enrich_draft(raw)
            if not should_schedule_polling(draft):
                continue
            scheduled += 1
            event_id = self._store.upsert_event(draft)
            n = self._store.ensure_triggers(
                event_id,
                draft.event_at_utc,
                draft.source_ids,
                self._offsets,
            )
            triggers += n

            if economic_calendar is not None:
                try:
                    existing_ids = {e.id for e in economic_calendar._events if e.id}
                    if event_id not in existing_ids:
                        economic_calendar.add_event(
                            EconomicEvent(
                                id=event_id,
                                event_name=draft.event_name,
                                event_type=draft.event_type,
                                scheduled_at=draft.event_at_utc,
                                impact_level=draft.impact_level,
                                affected_symbols=draft.affected_symbols,
                                country=draft.country,
                                source=_provider_to_news_source(draft.provider_id),
                            )
                        )
                        registered += 1
                except Exception as exc:
                    logger.warning("CalendarSync: economic register failed: %s", exc)

        cleaned = self._store.cleanup_completed_events()
        return {
            "providers": self._registry.list_providers(),
            "drafts": len(drafts),
            "scheduled": scheduled,
            "triggers_created": triggers,
            "economic_registered": registered,
            "events_cleaned": cleaned,
        }


def _provider_to_news_source(provider_id: str) -> NewsSource:
    mapping = {
        "fred": NewsSource.FRED,
        "finnhub": NewsSource.FINNHUB,
        "fmp": NewsSource.FMP,
        "static_us_macro": NewsSource.FRED,
    }
    return mapping.get(provider_id, NewsSource.FRED)
