"""Deterministic news scheduler: baseline interval + calendar event triggers."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone

from agents.news.calendar.calendar_store import CalendarScheduleStore
from agents.news.calendar.calendar_sync import CalendarSyncService

logger = logging.getLogger(__name__)


class NewsCalendarScheduler:
    """
    Runs:
      - Full news ingest every baseline_interval_seconds (default 4h)
      - Per-source ingest at calendar trigger times (HIGH/CRITICAL only)
      - Catch-up for triggers missed in the last N minutes on startup
    """

    def __init__(
        self,
        agent,
        *,
        baseline_interval_seconds: int = 14400,
        max_triggers_per_day: int = 50,
        catchup_minutes: int = 30,
        sync_interval_seconds: int = 21600,
        poll_tick_seconds: float = 15.0,
        store: CalendarScheduleStore | None = None,
        sync_service: CalendarSyncService | None = None,
    ) -> None:
        self._agent = agent
        self._baseline_interval = baseline_interval_seconds
        self._max_triggers_per_day = max_triggers_per_day
        self._catchup_minutes = catchup_minutes
        self._sync_interval = sync_interval_seconds
        self._poll_tick = poll_tick_seconds
        self._store = store or CalendarScheduleStore()
        self._sync = sync_service or CalendarSyncService(store=self._store)
        self._running = False
        self._task: asyncio.Task | None = None
        self._next_baseline_mono: float | None = None
        self._next_sync_mono: float | None = None
        self._triggers_today = 0
        self._trigger_day: date | None = None

    def start_background(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def run(self) -> None:
        self._running = True
        now_mono = time.monotonic()
        self._next_baseline_mono = now_mono
        self._next_sync_mono = now_mono

        await self._sync.sync(self._agent._calendar)
        await self._run_catchup()
        self._next_baseline_mono = time.monotonic() + self._baseline_interval
        self._next_sync_mono = time.monotonic() + self._sync_interval

        logger.info(
            "NewsCalendarScheduler started | baseline=%ds | max_triggers/day=%d",
            self._baseline_interval,
            self._max_triggers_per_day,
        )

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("NewsCalendarScheduler tick error: %s", exc)
            await asyncio.sleep(self._sleep_duration())

    async def _tick(self) -> None:
        now_utc = datetime.now(timezone.utc)
        self._reset_daily_counter(now_utc)

        due = self._store.fetch_due_triggers(now_utc, limit=10)
        for trigger in due:
            if self._triggers_today >= self._max_triggers_per_day:
                logger.warning("NewsCalendarScheduler: daily trigger cap reached")
                break
            sources = [s for s in trigger.source_ids if s]
            if not sources:
                self._store.mark_trigger_fired_and_delete(trigger.id)
                continue
            logger.info(
                "Calendar trigger: offset=%+dm sources=%s",
                trigger.offset_minutes,
                sources,
            )
            await self._agent.run_sources(sources)
            self._store.mark_trigger_fired_and_delete(trigger.id)
            self._triggers_today += 1

        self._store.cleanup_completed_events()

        if time.monotonic() >= (self._next_sync_mono or 0):
            await self._sync.sync(self._agent._calendar)
            self._next_sync_mono = time.monotonic() + self._sync_interval

        if time.monotonic() >= (self._next_baseline_mono or 0):
            await self._agent.run_once()
            await self._sync.sync(self._agent._calendar)
            self._next_baseline_mono = time.monotonic() + self._baseline_interval

    async def _run_catchup(self) -> None:
        now = datetime.now(timezone.utc)
        missed = self._store.fetch_catchup_triggers(now, self._catchup_minutes)
        if not missed:
            return
        logger.info("NewsCalendarScheduler: catch-up %d missed triggers", len(missed))
        for trigger in missed:
            if self._triggers_today >= self._max_triggers_per_day:
                break
            sources = [s for s in trigger.source_ids if s]
            if sources:
                await self._agent.run_sources(sources)
            self._store.mark_trigger_fired_and_delete(trigger.id)
            self._triggers_today += 1

    def _sleep_duration(self) -> float:
        now_mono = time.monotonic()
        candidates = [self._poll_tick]
        if self._next_baseline_mono:
            candidates.append(max(0.0, self._next_baseline_mono - now_mono))
        next_trigger = self._store.next_pending_trigger_at()
        if next_trigger:
            delta = (next_trigger - datetime.now(timezone.utc)).total_seconds()
            candidates.append(max(0.0, min(delta, self._poll_tick * 4)))
        return min(candidates)

    def _reset_daily_counter(self, now: datetime) -> None:
        day = now.date()
        if self._trigger_day != day:
            self._trigger_day = day
            since = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            self._triggers_today = self._store.count_triggers_fired_since(since)

    def status(self) -> dict:
        nxt = self._store.next_pending_trigger_at()
        return {
            "baseline_interval_seconds": self._baseline_interval,
            "max_triggers_per_day": self._max_triggers_per_day,
            "triggers_fired_today": self._triggers_today,
            "catchup_minutes": self._catchup_minutes,
            "next_trigger_at": nxt.isoformat() if nxt else None,
        }
