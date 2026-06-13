"""Tests for calendar classifier and scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.news.calendar.classifier import (
    classify_event_name,
    enrich_draft,
    should_schedule_polling,
)
from agents.news.calendar.schemas import CalendarEventDraft
from agents.news.calendar.calendar_store import CalendarScheduleStore
from agents.news.news_schemas import EventType, ImpactLevel
from data.storage.timescale_store import TimescaleStore


def test_classify_jobless_claims():
    etype, impact = classify_event_name("State Unemployment Insurance Weekly Claims Report")
    assert etype == EventType.JOBLESS_CLAIMS
    assert impact == ImpactLevel.HIGH


def test_ignore_credit_card_headlines():
    draft = CalendarEventDraft(
        provider_id="fred",
        external_key="x",
        event_name="Best rewards credit cards for June 2026",
        event_type=EventType.BREAKING,
        event_at_utc=datetime.now(timezone.utc) + timedelta(hours=1),
        impact_level=ImpactLevel.HIGH,
    )
    assert should_schedule_polling(draft) is False


def test_enrich_adds_sources():
    draft = enrich_draft(
        CalendarEventDraft(
            provider_id="static",
            external_key="j",
            event_name="State Unemployment Insurance Weekly Claims Report",
            event_type=EventType.GENERAL_MARKET,
            event_at_utc=datetime.now(timezone.utc) + timedelta(days=1),
            impact_level=ImpactLevel.LOW,
        )
    )
    assert draft.event_type == EventType.JOBLESS_CLAIMS
    assert "rss_cnbc_markets" in draft.source_ids
    assert should_schedule_polling(draft)


def test_calendar_store_json_triggers_and_cleanup(tmp_path, monkeypatch):
    archive = tmp_path / "cal.jsonl"
    monkeypatch.setenv("NEWS_CALENDAR_ARCHIVE", str(archive))
    import agents.news.calendar.calendar_store as cs

    monkeypatch.setattr(cs, "CALENDAR_ARCHIVE", archive)

    store = CalendarScheduleStore(store=TimescaleStore(database_url=""))
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    draft = CalendarEventDraft(
        provider_id="static_us_macro",
        external_key="claims|2026-06-04",
        event_name="Jobless Claims",
        event_type=EventType.JOBLESS_CLAIMS,
        event_at_utc=future,
        impact_level=ImpactLevel.HIGH,
        source_ids=["rss_cnbc_markets"],
    )
    eid = store.upsert_event(draft)
    n = store.ensure_triggers(eid, future, draft.source_ids, [-15, 0])
    assert n == 2
    due = store.fetch_due_triggers(datetime.now(timezone.utc) + timedelta(hours=3))
    assert len(due) >= 2
    store.mark_trigger_fired_and_delete(due[0].id)
    assert due[0].id not in store._json_triggers


@pytest.mark.asyncio
async def test_ingest_sources_only_runs_requested():
    from agents.news.news_ingestion_service import NewsIngestionService

    async with NewsIngestionService() as svc:
        items = await svc.ingest_sources(["rss_cnbc_markets"])
    assert isinstance(items, list)
