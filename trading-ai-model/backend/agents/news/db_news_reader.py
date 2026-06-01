"""
Read-only news view for the chart watcher.

The API process owns MarketNewsAgent ingestion + calendar polling.
The watcher refreshes classified events and risk windows from Postgres.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_db_store import NewsDbStore
from agents.news.news_risk_filter_service import NewsRiskFilterService
from agents.news.news_schemas import NewsEvent, NewsFeatures, NewsMode
from config.settings import get_settings

logger = logging.getLogger(__name__)

MAX_CACHE_HOURS = 6


class DbNewsReader:
    """DB-backed news reader — no RSS/API polling."""

    def __init__(
        self,
        db_store: NewsDbStore | None = None,
        refresh_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self._db = db_store or NewsDbStore()
        self._refresh_seconds = refresh_seconds or settings.watcher_news_refresh_seconds
        self._calendar = EconomicCalendarService(
            store=self._db.store if self._db.available else None
        )
        self._risk = NewsRiskFilterService(self._calendar)
        self._events: list[NewsEvent] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_refresh: datetime | None = None

        logger.info(
            "DbNewsReader initialized | db=%s | refresh=%ds",
            self._db.available,
            self._refresh_seconds,
        )

    async def start_refresh(self) -> None:
        """Load cache once and start periodic DB refresh (no ingestion)."""
        await self.refresh()
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def refresh(self) -> None:
        if self._db.available:
            self._calendar.hydrate_from_store()
            self._events = await self._db.fetch_recent_news_events(hours=MAX_CACHE_HOURS)
            self._last_refresh = datetime.now(timezone.utc)
            logger.debug("DbNewsReader: refreshed %d events from DB", len(self._events))
        else:
            logger.warning(
                "DbNewsReader: database unavailable — news features will be empty until API + DB are up"
            )

    async def _refresh_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._refresh_seconds)
                await self.refresh()
        except asyncio.CancelledError:
            pass

    def get_news_features(
        self,
        symbol: str,
        technical_direction: int = 0,
        at: Optional[datetime] = None,
    ) -> NewsFeatures:
        recent = self._get_recent_events(hours=2, symbol=symbol)
        return self._risk.compute_features(symbol, recent, technical_direction, at)

    def get_recent_events(
        self,
        symbol: Optional[str] = None,
        hours: int = 24,
    ) -> list[NewsEvent]:
        return self._get_recent_events(hours=hours, symbol=symbol)

    def get_latest_explanation(self, symbol: str) -> str:
        events = self._get_recent_events(hours=2, symbol=symbol)
        if not events:
            return "No significant news activity for this symbol in the last 2 hours."

        high_impact = [e for e in events if e.impact_score >= 0.60]
        lines = ["Current news context (from DB):"]
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
        return {
            "mode": "db_reader",
            "db_connected": self._db.available,
            "cached_events": len(self._events),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "refresh_seconds": self._refresh_seconds,
        }

    def _get_recent_events(
        self,
        hours: int = 2,
        symbol: Optional[str] = None,
    ) -> list[NewsEvent]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result: list[NewsEvent] = []
        for e in self._events:
            published = (
                e.published_at
                if e.published_at.tzinfo
                else e.published_at.replace(tzinfo=timezone.utc)
            )
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
