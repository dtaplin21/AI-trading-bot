"""Pluggable calendar providers — register new sources without changing the scheduler."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from agents.news.calendar.schemas import CalendarEventDraft

logger = logging.getLogger(__name__)


@runtime_checkable
class CalendarProvider(Protocol):
    provider_id: str

    async def fetch_events(self, days_ahead: int) -> list[CalendarEventDraft]: ...


class CalendarProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, CalendarProvider] = {}

    def register(self, provider: CalendarProvider) -> None:
        pid = provider.provider_id
        if pid in self._providers:
            logger.warning("CalendarProviderRegistry: replacing provider %s", pid)
        self._providers[pid] = provider
        logger.info("CalendarProviderRegistry: registered %s", pid)

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    async def fetch_all(self, days_ahead: int) -> list[CalendarEventDraft]:
        out: list[CalendarEventDraft] = []
        for pid, provider in self._providers.items():
            try:
                batch = await provider.fetch_events(days_ahead)
                out.extend(batch)
                logger.info("CalendarProvider %s: %d events", pid, len(batch))
            except Exception as exc:
                logger.error("CalendarProvider %s failed: %s", pid, exc)
        return out


_registry: CalendarProviderRegistry | None = None


def get_calendar_registry() -> CalendarProviderRegistry:
    global _registry
    if _registry is None:
        _registry = CalendarProviderRegistry()
        _register_builtin_providers(_registry)
    return _registry


def reset_calendar_registry() -> None:
    global _registry
    _registry = None


def _register_builtin_providers(registry: CalendarProviderRegistry) -> None:
    from agents.news.calendar.providers.fmp_provider import FmpCalendarProvider
    from agents.news.calendar.providers.fred_provider import FredCalendarProvider
    from agents.news.calendar.providers.finnhub_provider import FinnhubCalendarProvider
    from agents.news.calendar.providers.static_us_macro import StaticUsMacroProvider

    registry.register(StaticUsMacroProvider())
    registry.register(FredCalendarProvider())
    registry.register(FinnhubCalendarProvider())
    registry.register(FmpCalendarProvider())
