"""MarketNewsAgent — orchestrates news ingestion, calendar scheduling, and classification."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.news.calendar.calendar_scheduler import NewsCalendarScheduler
from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_db_store import NewsDbStore
from agents.news.news_ingestion_service import NewsIngestionService
from agents.news.news_risk_filter_service import NewsRiskFilterService
from agents.news.news_schemas import (
    EconomicEvent,
    NewsEvent,
    NewsFeatures,
    NewsMode,
    RawNewsItem,
    SymbolNewsImpact,
)
from agents.news.news_sentiment_service import NewsSentimentService
from agents.news.news_symbol_mapper import NewsSymbolMapper
from config.settings import get_settings

logger = logging.getLogger(__name__)

MAX_CACHE_HOURS = 6
MAX_EVENTS_CACHE = 500


class MarketNewsAgent:
    """Orchestrates the full Market News Intelligence pipeline."""

    def __init__(
        self,
        use_llm: bool = True,
        polling_interval: int | None = None,
        db_store: NewsDbStore | None = None,
    ) -> None:
        settings = get_settings()
        self._polling_interval = polling_interval or settings.news_polling_interval_seconds
        self._db = db_store or NewsDbStore()
        self._calendar = EconomicCalendarService(
            store=self._db.store if self._db.available else None
        )
        if self._db.available:
            self._calendar.hydrate_from_store()
        if settings.news_load_default_calendar:
            self._calendar.load_default_session_events()
        self._sentiment = NewsSentimentService(use_llm=use_llm and settings.llm_enabled)
        self._mapper = NewsSymbolMapper()
        self._risk = NewsRiskFilterService(self._calendar)

        self._events: list[NewsEvent] = []
        self._symbol_impacts: list[SymbolNewsImpact] = []
        self._running = False
        self._last_run: Optional[datetime] = None
        self._error_count = 0
        self._task: Optional[asyncio.Task] = None
        settings = get_settings()
        offsets = [int(x.strip()) for x in settings.news_calendar_trigger_offsets.split(",") if x.strip()]
        from agents.news.calendar.calendar_store import CalendarScheduleStore
        from agents.news.calendar.calendar_sync import CalendarSyncService

        store = CalendarScheduleStore()
        sync = CalendarSyncService(
            store=store,
            days_ahead=settings.news_calendar_days_ahead,
            trigger_offsets_minutes=offsets or [-15, 0, 5],
        )
        self._scheduler = NewsCalendarScheduler(
            self,
            baseline_interval_seconds=self._polling_interval,
            max_triggers_per_day=settings.news_calendar_max_triggers_per_day,
            catchup_minutes=settings.news_calendar_catchup_minutes,
            sync_interval_seconds=settings.news_calendar_sync_interval_seconds,
            store=store,
            sync_service=sync,
        )

        logger.info(
            "MarketNewsAgent initialized | baseline=%ds | calendar_triggers/day=%d",
            self._polling_interval,
            settings.news_calendar_max_triggers_per_day,
        )

    async def start(self) -> None:
        """Legacy loop — prefer scheduler via start_background()."""
        self._running = True
        await self._scheduler.run()

    def start_background(self) -> None:
        """Start calendar-aware scheduler (baseline + event triggers)."""
        if self._task and not self._task.done():
            return
        self._running = True
        self._scheduler.start_background()
        self._task = self._scheduler._task

    async def stop(self) -> None:
        self._running = False
        await self._scheduler.stop()
        logger.info("MarketNewsAgent: stopped")

    async def run_once(self) -> list[NewsEvent]:
        async with NewsIngestionService() as ingestion:
            raw_items = await ingestion.ingest_all()
        return await self._process_raw_items(raw_items)

    async def run_sources(self, source_ids: list[str]) -> list[NewsEvent]:
        async with NewsIngestionService() as ingestion:
            raw_items = await ingestion.ingest_sources(source_ids)
        return await self._process_raw_items(raw_items)

    async def _process_raw_items(self, raw_items: list[RawNewsItem]) -> list[NewsEvent]:
        if not raw_items:
            self._last_run = datetime.now(timezone.utc)
            return []

        classified = await self._sentiment.classify_batch(raw_items)
        new_impacts: list[SymbolNewsImpact] = []
        new_events: list[NewsEvent] = []

        for event in classified:
            if not event.headline:
                continue
            event.id = str(uuid.uuid4())
            impacts = self._mapper.map(event)
            for imp in impacts:
                imp.news_event_id = event.id
            new_events.append(event)
            new_impacts.extend(impacts)
            if event.news_mode == NewsMode.RISK_EVENT and event.urgency_score > 0.80:
                self._escalate_breaking(event)

        if self._db.available:
            asyncio.create_task(self._store_to_db(new_events, new_impacts))

        self._events.extend(new_events)
        self._symbol_impacts.extend(new_impacts)
        self._prune_cache()
        self._last_run = datetime.now(timezone.utc)

        logger.info(
            "MarketNewsAgent: processed %d items | cache=%d",
            len(new_events),
            len(self._events),
        )
        return new_events

    def get_news_features(
        self,
        symbol: str,
        technical_direction: int = 0,
        at: Optional[datetime] = None,
    ) -> NewsFeatures:
        recent = self._get_recent_events(hours=2)
        return self._risk.compute_features(symbol, recent, technical_direction, at)

    def is_trading_blocked(self, symbol: str) -> tuple[bool, str]:
        blocked, reason = self._calendar.is_trading_blocked(symbol)
        if blocked:
            return True, reason
        features = self.get_news_features(symbol)
        if features.trading_blocked:
            return True, features.news_risk_reason or "High-impact news active"
        return False, ""

    def get_size_reduction_factor(self, symbol: str) -> float:
        return self._calendar.get_size_reduction_factor(symbol)

    def requires_manual_approval(self, symbol: str) -> bool:
        return self._calendar.requires_manual_approval(symbol)

    def add_economic_event(self, event: EconomicEvent) -> None:
        self._calendar.add_event(event)

    def add_economic_events_bulk(self, events: list[EconomicEvent]) -> None:
        self._calendar.add_events_bulk(events)

    def get_recent_events(
        self,
        symbol: Optional[str] = None,
        hours: int = 24,
    ) -> list[NewsEvent]:
        return self._get_recent_events(hours=hours, symbol=symbol)

    def get_upcoming_events(self, hours_ahead: int = 24) -> list[EconomicEvent]:
        return self._calendar.get_upcoming_events(hours_ahead)

    def get_latest_explanation(self, symbol: str) -> str:
        events = self._get_recent_events(hours=2, symbol=symbol)
        if not events:
            return "No significant news activity for this symbol in the last 2 hours."

        high_impact = [e for e in events if e.impact_score >= 0.60]
        lines = ["Current news context:"]
        for e in high_impact[:3]:
            lines.append(
                f"  • [{e.event_type.value.upper()}] {e.headline[:80]} "
                f"(impact={e.impact_score:.2f}, sentiment={e.sentiment_label.value})"
            )
        if not high_impact:
            lines.append(f"  • Most recent: {events[0].headline[:80]}")

        blocked, reason = self._calendar.is_trading_blocked(symbol)
        if blocked:
            lines.append(f"  ⚠ Trading currently BLOCKED: {reason}")
        elif self._calendar.get_size_reduction_factor(symbol) < 1.0:
            lines.append("  ⚠ Position size reduction recommended due to news risk")

        return "\n".join(lines)

    def get_status(self) -> dict:
        sched = self._scheduler.status()
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "cached_events": len(self._events),
            "error_count": self._error_count,
            "polling_interval": self._polling_interval,
            "db_connected": self._db.available,
            "calendar_scheduler": sched,
        }

    def _get_recent_events(
        self,
        hours: int = 2,
        symbol: Optional[str] = None,
    ) -> list[NewsEvent]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = []
        for e in self._events:
            published = e.published_at if e.published_at.tzinfo else e.published_at.replace(tzinfo=timezone.utc)
            if published < cutoff:
                continue
            if symbol:
                sym_upper = symbol.upper()
                affected = [s.upper() for s in e.symbols_affected]
                if sym_upper not in affected and e.news_mode != NewsMode.RISK_EVENT:
                    continue
            result.append(e)
        result.sort(key=lambda e: e.published_at, reverse=True)
        return result

    def _escalate_breaking(self, event: NewsEvent) -> None:
        logger.warning(
            "MarketNewsAgent: BREAKING escalation | '%s' | impact=%.2f | symbols=%s",
            event.headline[:80],
            event.impact_score,
            event.symbols_affected,
        )
        self._calendar.add_breaking_event(
            event_name=event.headline[:100],
            symbol_override=event.symbols_affected or None,
        )

    def _prune_cache(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_CACHE_HOURS)
        before = len(self._events)
        self._events = [
            e
            for e in self._events
            if (e.published_at if e.published_at.tzinfo else e.published_at.replace(tzinfo=timezone.utc)) > cutoff
        ]
        if len(self._events) > MAX_EVENTS_CACHE:
            self._events = sorted(self._events, key=lambda e: e.published_at, reverse=True)[:MAX_EVENTS_CACHE]
        pruned = before - len(self._events)
        if pruned > 0:
            logger.debug("MarketNewsAgent: pruned %d old events from cache", pruned)

    async def _store_to_db(
        self,
        events: list[NewsEvent],
        impacts: list[SymbolNewsImpact],
    ) -> None:
        if not self._db:
            return
        try:
            await self._db.insert_news_events(events)
            await self._db.insert_symbol_impacts(impacts)
        except Exception as e:
            logger.error("MarketNewsAgent: DB store failed: %s", e)
