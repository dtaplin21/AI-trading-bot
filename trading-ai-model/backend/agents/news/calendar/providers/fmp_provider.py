"""FMP economic calendar provider."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from agents.news.calendar.classifier import classify_event_name, default_sources_for
from agents.news.calendar.schemas import CalendarEventDraft
from agents.news.news_schemas import ImpactLevel

FMP_KEY = os.getenv("FMP_API_KEY", "")
HTTP_TIMEOUT = 10.0


class FmpCalendarProvider:
    provider_id = "fmp"

    async def fetch_events(self, days_ahead: int) -> list[CalendarEventDraft]:
        if not FMP_KEY:
            return []
        now = datetime.now(timezone.utc)
        start = now.strftime("%Y-%m-%d")
        end = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        url = "https://financialmodelingprep.com/api/v3/economic_calendar"
        params = {"apikey": FMP_KEY, "from": start, "to": end}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            rows = r.json()
        if not isinstance(rows, list):
            return []

        out: list[CalendarEventDraft] = []
        for row in rows:
            name = row.get("event") or "Economic event"
            event_type, impact = classify_event_name(name)
            fmp_impact = str(row.get("impact", "")).lower()
            if fmp_impact == "high":
                impact = ImpactLevel.HIGH if impact == ImpactLevel.LOW else impact
            if impact not in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}:
                continue
            date_raw = row.get("date")
            if not date_raw:
                continue
            try:
                event_at = datetime.fromisoformat(str(date_raw).replace("Z", "+00:00"))
                if event_at.tzinfo is None:
                    event_at = event_at.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if event_at <= now:
                continue
            key = f"{name}|{event_at.isoformat()}"
            out.append(
                CalendarEventDraft(
                    provider_id=self.provider_id,
                    external_key=key,
                    event_name=name,
                    event_type=event_type,
                    event_at_utc=event_at,
                    impact_level=impact,
                    source_ids=default_sources_for(event_type) + ["fmp_calendar"],
                    affected_symbols=[],
                    country=str(row.get("country", "US")),
                )
            )
        return out
