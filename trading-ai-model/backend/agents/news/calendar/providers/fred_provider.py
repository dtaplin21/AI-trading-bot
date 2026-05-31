"""FRED releases/dates API — dates only; default noon UTC when time unknown."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from agents.news.calendar.classifier import classify_event_name, default_sources_for
from agents.news.calendar.schemas import CalendarEventDraft
from agents.news.news_schemas import ImpactLevel

FRED_KEY = os.getenv("FRED_API_KEY", "")
HTTP_TIMEOUT = 10.0


class FredCalendarProvider:
    provider_id = "fred"

    async def fetch_events(self, days_ahead: int) -> list[CalendarEventDraft]:
        if not FRED_KEY:
            return []
        now = datetime.now(timezone.utc)
        params = {
            "api_key": FRED_KEY,
            "file_type": "json",
            "include_release_dates_with_no_data": "true",
            "realtime_start": now.strftime("%Y-%m-%d"),
            "realtime_end": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
            "sort_order": "asc",
            "limit": 500,
        }
        url = "https://api.stlouisfed.org/fred/releases/dates"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()

        out: list[CalendarEventDraft] = []
        for row in data.get("release_dates") or []:
            name = row.get("release_name") or "Economic release"
            date_str = row.get("date")
            if not date_str:
                continue
            event_type, impact = classify_event_name(name)
            if impact not in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}:
                continue
            # FRED API: date only — use 13:30 UTC (~8:30 ET) for US macro defaults
            event_at = datetime.fromisoformat(f"{date_str}T13:30:00+00:00")
            if event_at <= now:
                continue
            rid = row.get("release_id", name)
            out.append(
                CalendarEventDraft(
                    provider_id=self.provider_id,
                    external_key=f"{rid}|{date_str}",
                    event_name=name,
                    event_type=event_type,
                    event_at_utc=event_at,
                    impact_level=impact,
                    source_ids=default_sources_for(event_type),
                    affected_symbols=[],
                )
            )
        return out
