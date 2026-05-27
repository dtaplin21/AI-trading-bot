"""
agents/news/economic_calendar_service.py

Manages the economic event schedule and creates NewsRiskWindows
for high-impact events. These windows tell the Risk Agent exactly
when to block, reduce, or flag trades for manual approval.

Pre-event window:  15 min before release (configurable per event)
Post-event window: 15 min after release (configurable per event)
Breaking event:    Immediate block until volatility subsides
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.news.news_schemas import (
    EconomicEvent,
    EventType,
    ImpactLevel,
    NewsRiskWindow,
    VolatilityRisk,
)

logger = logging.getLogger(__name__)

RISK_WINDOW_CONFIG: dict[EventType, tuple[int, int, bool, bool, bool]] = {
    EventType.FOMC: (30, 30, False, True, True),
    EventType.CPI: (15, 15, False, True, False),
    EventType.NFP: (15, 15, False, True, False),
    EventType.PPI: (15, 15, False, True, False),
    EventType.GDP: (15, 15, False, True, False),
    EventType.FED_SPEECH: (10, 10, False, True, False),
    EventType.FED_POLICY: (15, 20, False, True, True),
    EventType.JOBLESS_CLAIMS: (10, 10, True, True, False),
    EventType.OIL_INVENTORY: (5, 5, True, True, False),
    EventType.TREASURY_YIELD: (5, 5, True, True, False),
    EventType.EARNINGS: (5, 5, True, True, False),
    EventType.GEOPOLITICAL: (0, 30, False, True, True),
    EventType.BREAKING: (0, 20, False, True, True),
    EventType.CONTRACT_EXPIRY: (15, 5, True, True, False),
}

EVENT_AFFECTED_SYMBOLS: dict[EventType, list[str]] = {
    EventType.FOMC: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN", "6E", "GC"],
    EventType.CPI: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN", "GC"],
    EventType.NFP: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN", "6E"],
    EventType.PPI: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN"],
    EventType.GDP: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN"],
    EventType.FED_SPEECH: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN"],
    EventType.FED_POLICY: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN", "6E", "GC"],
    EventType.JOBLESS_CLAIMS: ["ES", "MES", "NQ", "MNQ"],
    EventType.OIL_INVENTORY: ["CL", "QM", "ES", "MES"],
    EventType.TREASURY_YIELD: ["ZB", "ZN", "ZF", "ZT", "ES", "MES"],
    EventType.EARNINGS: ["ES", "MES", "NQ", "MNQ"],
    EventType.GEOPOLITICAL: ["ES", "MES", "NQ", "MNQ", "CL", "GC", "6E"],
    EventType.BREAKING: ["ES", "MES", "NQ", "MNQ", "CL", "GC"],
    EventType.CONTRACT_EXPIRY: ["ES", "MES", "NQ", "MNQ", "ZB", "ZN", "CL"],
}


class EconomicCalendarService:
    """
    Tracks scheduled economic events and manages risk windows.
    Integrates with the Risk Agent to gate trade approvals.
    """

    def __init__(self, store=None) -> None:
        self._events: list[EconomicEvent] = []
        self._windows: list[NewsRiskWindow] = []
        self._store = store

    def add_event(self, event: EconomicEvent) -> None:
        """Register a scheduled economic event and create its risk windows."""
        if not event.id:
            event.id = str(uuid.uuid4())
        self._events.append(event)
        windows = self._create_windows(event)
        for w in windows:
            if not w.id:
                w.id = str(uuid.uuid4())
        self._windows.extend(windows)
        if self._store and self._store.available:
            self._store.insert_economic_event(event)
            self._store.insert_risk_windows(windows)
        logger.info(
            "CalendarService: registered event '%s' at %s → %d risk windows",
            event.event_name,
            event.scheduled_at.isoformat(),
            len(windows),
        )

    def add_events_bulk(self, events: list[EconomicEvent]) -> None:
        for e in events:
            self.add_event(e)

    def add_breaking_event(
        self,
        event_name: str,
        symbol_override: Optional[list[str]] = None,
    ) -> NewsRiskWindow:
        """
        Immediately create a risk window for a breaking/unexpected event.
        No pre-window — starts now. Post-window is 20 minutes.
        """
        now = datetime.now(tz=timezone.utc)
        symbols = symbol_override or EVENT_AFFECTED_SYMBOLS.get(EventType.BREAKING, [])
        window = NewsRiskWindow(
            id=str(uuid.uuid4()),
            event_name=event_name,
            event_type=EventType.BREAKING,
            starts_at=now,
            ends_at=now + timedelta(minutes=20),
            affected_symbols=symbols,
            risk_level=VolatilityRisk.EXTREME,
            trading_allowed=False,
            reduce_size=True,
            require_manual=True,
            reason=f"Breaking event: {event_name}",
        )
        self._windows.append(window)
        if self._store and self._store.available:
            self._store.insert_risk_windows([window])
        logger.warning("BREAKING event window opened: %s", event_name)
        return window

    def hydrate_from_store(self, hours_ahead: int = 72) -> None:
        """Load upcoming economic events from DB (windows queried live)."""
        if not self._store or not self._store.available:
            return
        existing = {(e.event_name, self._event_time(e).isoformat()) for e in self._events}
        for event in self._store.fetch_upcoming_economic_events(hours_ahead):
            key = (event.event_name, self._event_time(event).isoformat())
            if key not in existing:
                self._events.append(event)
                existing.add(key)
        logger.info("CalendarService: hydrated %d economic events from DB", len(self._events))

    def get_active_windows(
        self,
        symbol: str,
        at: Optional[datetime] = None,
    ) -> list[NewsRiskWindow]:
        """Returns all currently active risk windows for a given symbol."""
        now = at or datetime.now(tz=timezone.utc)
        active = []
        for w in self._windows:
            starts = w.starts_at if w.starts_at.tzinfo else w.starts_at.replace(tzinfo=timezone.utc)
            ends = w.ends_at if w.ends_at.tzinfo else w.ends_at.replace(tzinfo=timezone.utc)
            if starts <= now <= ends:
                sym_upper = symbol.upper()
                if not w.affected_symbols or sym_upper in [s.upper() for s in w.affected_symbols]:
                    active.append(w)
        if self._store and self._store.available:
            db_windows = self._store.fetch_active_risk_windows(symbol, now)
            active = self._merge_windows(active, db_windows)
        return active

    def _merge_windows(
        self,
        local: list[NewsRiskWindow],
        remote: list[NewsRiskWindow],
    ) -> list[NewsRiskWindow]:
        seen: set[tuple[str, str]] = set()
        merged: list[NewsRiskWindow] = []
        for w in local + remote:
            key = (w.event_name, self._window_start(w).isoformat())
            if key in seen:
                continue
            seen.add(key)
            merged.append(w)
        return merged

    def _window_start(self, w: NewsRiskWindow) -> datetime:
        t = w.starts_at
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def is_trading_blocked(self, symbol: str, at: Optional[datetime] = None) -> tuple[bool, str]:
        """Quick boolean check: is trading blocked right now for this symbol?"""
        windows = self.get_active_windows(symbol, at)
        for w in windows:
            if not w.trading_allowed:
                return True, w.reason or f"{w.event_name} risk window active"
        return False, ""

    def get_size_reduction_factor(self, symbol: str, at: Optional[datetime] = None) -> float:
        """
        Returns the recommended position size multiplier (0.0–1.0).
        1.0 = full size. 0.5 = half size. 0.0 = no trades.
        """
        windows = self.get_active_windows(symbol, at)
        if not windows:
            return 1.0
        worst = max(windows, key=lambda w: self._vol_to_float(w.risk_level))
        if not worst.trading_allowed:
            return 0.0
        if worst.reduce_size:
            return 0.5
        return 1.0

    def requires_manual_approval(self, symbol: str, at: Optional[datetime] = None) -> bool:
        windows = self.get_active_windows(symbol, at)
        return any(w.require_manual for w in windows)

    def minutes_until_next_event(self, symbol: Optional[str] = None) -> float:
        """Returns minutes until the next high-impact scheduled event."""
        now = datetime.now(tz=timezone.utc)
        upcoming = []
        for event in self._events:
            scheduled = (
                event.scheduled_at
                if event.scheduled_at.tzinfo
                else event.scheduled_at.replace(tzinfo=timezone.utc)
            )
            if scheduled > now:
                if symbol is None or symbol.upper() in [s.upper() for s in event.affected_symbols]:
                    if event.impact_level in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}:
                        upcoming.append((scheduled - now).total_seconds() / 60.0)
        return min(upcoming) if upcoming else 9999.0

    def get_upcoming_events(self, hours_ahead: int = 24) -> list[EconomicEvent]:
        """Returns all high-impact events scheduled within the next N hours."""
        now = datetime.now(tz=timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [
            e
            for e in self._events
            if self._event_time(e) > now
            and self._event_time(e) <= cutoff
            and e.impact_level in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}
        ]

    def load_default_session_events(self) -> None:
        """Load placeholder CPI event for dev/demo sessions."""
        now = datetime.now(tz=timezone.utc)
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

    def _create_windows(self, event: EconomicEvent) -> list[NewsRiskWindow]:
        """Create pre-event and post-event risk windows for a scheduled event."""
        config = RISK_WINDOW_CONFIG.get(event.event_type)
        if not config:
            return []

        pre_min, post_min, allowed, reduce, manual = config
        scheduled = (
            event.scheduled_at
            if event.scheduled_at.tzinfo
            else event.scheduled_at.replace(tzinfo=timezone.utc)
        )
        symbols = event.affected_symbols or EVENT_AFFECTED_SYMBOLS.get(event.event_type, [])
        vol_risk = self._impact_to_vol(event.impact_level)
        windows = []

        if pre_min > 0:
            windows.append(
                NewsRiskWindow(
                    event_name=f"Pre-{event.event_name}",
                    event_type=event.event_type,
                    starts_at=scheduled - timedelta(minutes=pre_min),
                    ends_at=scheduled,
                    affected_symbols=symbols,
                    risk_level=vol_risk,
                    trading_allowed=allowed,
                    reduce_size=reduce,
                    require_manual=manual,
                    reason=f"{event.event_name} release in {pre_min} minutes",
                )
            )

        if post_min > 0:
            windows.append(
                NewsRiskWindow(
                    event_name=f"Post-{event.event_name}",
                    event_type=event.event_type,
                    starts_at=scheduled,
                    ends_at=scheduled + timedelta(minutes=post_min),
                    affected_symbols=symbols,
                    risk_level=vol_risk,
                    trading_allowed=allowed,
                    reduce_size=reduce,
                    require_manual=False,
                    reason=f"Post-{event.event_name} volatility window",
                )
            )

        return windows

    def _event_time(self, event: EconomicEvent) -> datetime:
        t = event.scheduled_at
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def _vol_to_float(self, risk: VolatilityRisk) -> float:
        return {
            VolatilityRisk.LOW: 0.1,
            VolatilityRisk.MEDIUM: 0.4,
            VolatilityRisk.HIGH: 0.7,
            VolatilityRisk.EXTREME: 1.0,
        }.get(risk, 0.0)

    def _impact_to_vol(self, level: ImpactLevel) -> VolatilityRisk:
        return {
            ImpactLevel.LOW: VolatilityRisk.LOW,
            ImpactLevel.MEDIUM: VolatilityRisk.MEDIUM,
            ImpactLevel.HIGH: VolatilityRisk.HIGH,
            ImpactLevel.CRITICAL: VolatilityRisk.EXTREME,
        }.get(level, VolatilityRisk.LOW)
