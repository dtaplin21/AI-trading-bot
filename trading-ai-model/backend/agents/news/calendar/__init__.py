"""Extensible economic calendar loading for event-triggered news polling."""

from agents.news.calendar.registry import CalendarProviderRegistry, get_calendar_registry
from agents.news.calendar.schemas import CalendarEventDraft, CalendarPollTrigger

__all__ = [
    "CalendarEventDraft",
    "CalendarPollTrigger",
    "CalendarProviderRegistry",
    "get_calendar_registry",
]
