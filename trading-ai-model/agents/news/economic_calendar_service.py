"""Economic calendar and news risk windows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.news.news_schemas import EconomicEvent, ImpactLevel, NewsRiskWindow


class EconomicCalendarService:
    """Manages scheduled macro events and breaking-news risk windows."""

    def __init__(self) -> None:
        self._scheduled: list[EconomicEvent] = []
        self._windows: list[NewsRiskWindow] = []

    def add_event(self, event: EconomicEvent) -> None:
        self._scheduled.append(event)

    def add_events_bulk(self, events: list[EconomicEvent]) -> None:
        self._scheduled.extend(events)

    def add_breaking_event(
        self,
        event_name: str,
        symbol_override: Optional[list[str]] = None,
        duration_minutes: int = 45,
    ) -> None:
        now = datetime.now(timezone.utc)
        symbols = symbol_override or ["MES", "ES", "NQ", "MNQ", "RTY", "YM"]
        for symbol in symbols:
            self._windows.append(
                NewsRiskWindow(
                    symbol=symbol.upper(),
                    reason=f"Breaking: {event_name}",
                    starts_at=now,
                    ends_at=now + timedelta(minutes=duration_minutes),
                    block_trading=True,
                    size_reduction=0.0,
                    requires_manual_approval=True,
                )
            )

    def get_upcoming_events(self, hours_ahead: int = 24) -> list[EconomicEvent]:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [e for e in self._scheduled if now <= self._event_time(e) <= cutoff]

    def is_trading_blocked(self, symbol: str) -> tuple[bool, str]:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)

        for w in self._active_windows(sym, now):
            if w.block_trading:
                return True, w.reason

        for event in self._scheduled:
            if sym not in [s.upper() for s in event.symbols] and event.symbols:
                continue
            t = self._event_time(event)
            start = t - timedelta(minutes=event.block_minutes_before)
            end = t + timedelta(minutes=event.block_minutes_after)
            if start <= now <= end and event.impact in (ImpactLevel.HIGH, ImpactLevel.CRITICAL):
                return True, f"Economic event blackout: {event.name}"

        return False, ""

    def get_size_reduction_factor(self, symbol: str) -> float:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)
        factor = 1.0

        for w in self._active_windows(sym, now):
            factor = min(factor, w.size_reduction)

        for event in self._scheduled:
            if event.symbols and sym not in [s.upper() for s in event.symbols]:
                continue
            t = self._event_time(event)
            start = t - timedelta(minutes=event.block_minutes_before * 2)
            end = t + timedelta(minutes=event.block_minutes_after)
            if start <= now <= end and event.impact == ImpactLevel.MEDIUM:
                factor = min(factor, event.size_reduction)

        return factor

    def requires_manual_approval(self, symbol: str) -> bool:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)
        return any(w.requires_manual_approval for w in self._active_windows(sym, now))

    def _active_windows(self, symbol: str, now: datetime) -> list[NewsRiskWindow]:
        return [
            w
            for w in self._windows
            if w.symbol == symbol and w.starts_at <= now <= w.ends_at
        ]

    def _event_time(self, event: EconomicEvent) -> datetime:
        t = event.scheduled_at
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def load_default_session_events(self) -> None:
        """Seed typical high-impact events for the current UTC day (dev/demo)."""
        now = datetime.now(timezone.utc)
        base = now.replace(hour=13, minute=30, second=0, microsecond=0)
        if base < now:
            base += timedelta(days=1)
        self.add_events_bulk(
            [
                EconomicEvent(
                    name="CPI Release",
                    scheduled_at=base,
                    impact=ImpactLevel.HIGH,
                    symbols=["MES", "ES", "NQ", "RTY"],
                    block_minutes_before=15,
                    block_minutes_after=30,
                    size_reduction=0.5,
                ),
            ]
        )
