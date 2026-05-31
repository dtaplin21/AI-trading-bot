"""US macro releases with reliable ET release times → stored as UTC."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agents.news.calendar.schemas import CalendarEventDraft
from agents.news.economic_calendar_service import EVENT_AFFECTED_SYMBOLS
from agents.news.news_schemas import EventType, ImpactLevel

ET = ZoneInfo(os.getenv("NEWS_CALENDAR_TZ", "America/New_York"))

# (weekday 0=Mon, hour, minute ET, event name, type, impact) — None weekday = daily skip
_STATIC_RELEASES: list[tuple[int | None, int, int, str, EventType, ImpactLevel]] = [
    (3, 8, 30, "State Unemployment Insurance Weekly Claims Report", EventType.JOBLESS_CLAIMS, ImpactLevel.HIGH),
]


def _et_to_utc(d: date, hour: int, minute: int) -> datetime:
    local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)
    return local.astimezone(timezone.utc)


class StaticUsMacroProvider:
    provider_id = "static_us_macro"

    async def fetch_events(self, days_ahead: int) -> list[CalendarEventDraft]:
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=days_ahead)
        out: list[CalendarEventDraft] = []

        d = today
        while d <= end:
            for rule in _STATIC_RELEASES:
                weekday, hour, minute, name, etype, impact = rule
                if weekday is not None and d.weekday() != weekday:
                    continue
                at_utc = _et_to_utc(d, hour, minute)
                if at_utc <= datetime.now(timezone.utc):
                    continue
                key = f"{name}|{at_utc.date().isoformat()}|{hour:02d}{minute:02d}"
                symbols = [s for s in EVENT_AFFECTED_SYMBOLS.get(etype, [])]
                out.append(
                    CalendarEventDraft(
                        provider_id=self.provider_id,
                        external_key=key,
                        event_name=name,
                        event_type=etype,
                        event_at_utc=at_utc,
                        impact_level=impact,
                        source_ids=[],
                        affected_symbols=symbols,
                    )
                )
            d += timedelta(days=1)
        return out
