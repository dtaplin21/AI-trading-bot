"""Economic calendar and news risk windows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.news.news_schemas import (
    EconomicEvent,
    EventType,
    ImpactLevel,
    NewsRiskWindow,
    VolatilityRisk,
)

IMPACT_WINDOWS: dict[ImpactLevel, dict] = {
    ImpactLevel.CRITICAL: {"before": 30, "after": 45, "block": True, "size": 0.25, "manual": True},
    ImpactLevel.HIGH: {"before": 15, "after": 30, "block": True, "size": 0.5, "manual": False},
    ImpactLevel.MEDIUM: {"before": 20, "after": 20, "block": False, "size": 0.75, "manual": False},
    ImpactLevel.LOW: {"before": 5, "after": 5, "block": False, "size": 1.0, "manual": False},
}

SIZE_BY_RISK = {
    VolatilityRisk.EXTREME: 0.0,
    VolatilityRisk.HIGH: 0.5,
    VolatilityRisk.MEDIUM: 0.75,
    VolatilityRisk.LOW: 1.0,
}


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
        symbols = [s.upper() for s in (symbol_override or ["MES", "ES", "NQ", "MNQ", "RTY", "YM"])]
        self._windows.append(
            NewsRiskWindow(
                event_name=event_name[:100],
                event_type=EventType.BREAKING,
                starts_at=now,
                ends_at=now + timedelta(minutes=duration_minutes),
                affected_symbols=symbols,
                risk_level=VolatilityRisk.EXTREME,
                trading_allowed=False,
                reduce_size=True,
                require_manual=True,
                reason=f"Breaking: {event_name}",
            )
        )

    def get_upcoming_events(self, hours_ahead: int = 24) -> list[EconomicEvent]:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [e for e in self._scheduled if now <= self._event_time(e) <= cutoff]

    def minutes_until_next_event(self, symbol: str) -> float:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)
        upcoming = []
        for event in self._scheduled:
            if event.affected_symbols and sym not in [s.upper() for s in event.affected_symbols]:
                continue
            t = self._event_time(event)
            if t > now:
                upcoming.append((t - now).total_seconds() / 60)
        return min(upcoming) if upcoming else 9999.0

    def is_trading_blocked(self, symbol: str) -> tuple[bool, str]:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)

        for w in self._active_windows(sym, now):
            if not w.trading_allowed:
                return True, w.reason

        for event in self._scheduled:
            if event.affected_symbols and sym not in [s.upper() for s in event.affected_symbols]:
                continue
            cfg = IMPACT_WINDOWS.get(event.impact_level, IMPACT_WINDOWS[ImpactLevel.LOW])
            if not cfg["block"]:
                continue
            t = self._event_time(event)
            start = t - timedelta(minutes=cfg["before"])
            end = t + timedelta(minutes=cfg["after"])
            if start <= now <= end:
                return True, f"Economic event blackout: {event.event_name}"

        return False, ""

    def get_size_reduction_factor(self, symbol: str) -> float:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)
        factor = 1.0

        for w in self._active_windows(sym, now):
            if w.reduce_size:
                factor = min(factor, SIZE_BY_RISK.get(w.risk_level, 0.5))

        for event in self._scheduled:
            if event.affected_symbols and sym not in [s.upper() for s in event.affected_symbols]:
                continue
            cfg = IMPACT_WINDOWS.get(event.impact_level, IMPACT_WINDOWS[ImpactLevel.LOW])
            t = self._event_time(event)
            start = t - timedelta(minutes=cfg["before"] * 2)
            end = t + timedelta(minutes=cfg["after"])
            if start <= now <= end:
                factor = min(factor, cfg["size"])

        return factor

    def requires_manual_approval(self, symbol: str) -> bool:
        sym = symbol.upper()
        now = datetime.now(timezone.utc)
        return any(w.require_manual for w in self._active_windows(sym, now))

    def _active_windows(self, symbol: str, now: datetime) -> list[NewsRiskWindow]:
        return [
            w
            for w in self._windows
            if symbol in [s.upper() for s in w.affected_symbols]
            and w.starts_at <= now <= w.ends_at
        ]

    def _event_time(self, event: EconomicEvent) -> datetime:
        t = event.scheduled_at
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def load_default_session_events(self) -> None:
        now = datetime.now(timezone.utc)
        base = now.replace(hour=13, minute=30, second=0, microsecond=0)
        if base < now:
            base += timedelta(days=1)
        self.add_events_bulk(
            [
                EconomicEvent(
                    event_name="CPI Release",
                    event_type=EventType.CPI,
                    scheduled_at=base,
                    impact_level=ImpactLevel.HIGH,
                    affected_symbols=["MES", "ES", "NQ", "RTY"],
                ),
            ]
        )
