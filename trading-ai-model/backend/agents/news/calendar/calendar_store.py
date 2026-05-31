"""Persist calendar events and poll triggers (DB + JSONL fallback)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from agents.news.calendar.schemas import CalendarEventDraft, CalendarPollTrigger
from data.storage.timescale_store import TimescaleStore

logger = logging.getLogger(__name__)

CALENDAR_ARCHIVE = Path(os.getenv("NEWS_CALENDAR_ARCHIVE", "logs/calendar_schedule.jsonl"))


class CalendarScheduleStore:
    """Upsert events, manage triggers, delete fired rows to limit clutter."""

    def __init__(self, store: TimescaleStore | None = None) -> None:
        self._store = store or TimescaleStore()
        self._json_events: dict[str, dict] = {}
        self._json_triggers: dict[str, dict] = {}
        if not self._store.available:
            self._load_json_archive()

    @property
    def available(self) -> bool:
        return self._store.available

    def upsert_event(self, draft: CalendarEventDraft) -> str:
        if self._store.available:
            return self._store.upsert_calendar_event(draft)
        eid = str(uuid.uuid4())
        row = draft.model_dump(mode="json")
        row["id"] = eid
        row["event_at_utc"] = draft.event_at_utc.isoformat()
        self._json_events[f"{draft.provider_id}|{draft.external_key}"] = row
        self._append_json({"type": "event", **row})
        return eid

    def ensure_triggers(
        self,
        event_id: str,
        event_at_utc: datetime,
        source_ids: list[str],
        offsets_minutes: list[int],
    ) -> int:
        created = 0
        for offset in offsets_minutes:
            trigger_at = event_at_utc + timedelta(minutes=offset)
            if trigger_at <= datetime.now(timezone.utc):
                continue
            if self._store.available:
                if self._store.insert_calendar_trigger(event_id, trigger_at, offset, source_ids):
                    created += 1
            else:
                tid = str(uuid.uuid4())
                key = f"{event_id}|{offset}"
                if key in self._json_triggers:
                    continue
                row = {
                    "id": tid,
                    "event_id": event_id,
                    "trigger_at_utc": trigger_at.isoformat(),
                    "offset_minutes": offset,
                    "source_ids": source_ids,
                    "status": "pending",
                }
                self._json_triggers[tid] = row
                self._append_json({"type": "trigger", **row})
                created += 1
        return created

    def fetch_due_triggers(self, now: datetime, limit: int = 20) -> list[CalendarPollTrigger]:
        if self._store.available:
            return self._store.fetch_due_calendar_triggers(now, limit)
        out: list[CalendarPollTrigger] = []
        for row in self._json_triggers.values():
            if row.get("status") != "pending":
                continue
            at = datetime.fromisoformat(row["trigger_at_utc"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at <= now:
                out.append(self._row_to_trigger(row))
        out.sort(key=lambda t: t.trigger_at_utc)
        return out[:limit]

    def fetch_catchup_triggers(self, now: datetime, lookback_minutes: int) -> list[CalendarPollTrigger]:
        cutoff = now - timedelta(minutes=lookback_minutes)
        if self._store.available:
            return self._store.fetch_catchup_calendar_triggers(now, cutoff)
        out: list[CalendarPollTrigger] = []
        for row in self._json_triggers.values():
            if row.get("status") != "pending":
                continue
            at = datetime.fromisoformat(row["trigger_at_utc"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if cutoff <= at <= now:
                out.append(self._row_to_trigger(row))
        return out

    def delete_trigger(self, trigger_id: str) -> None:
        if self._store.available:
            self._store.delete_calendar_trigger(trigger_id)
        else:
            self._json_triggers.pop(trigger_id, None)

    def cleanup_completed_events(self) -> int:
        if self._store.available:
            return self._store.cleanup_calendar_events()
        removed = 0
        now = datetime.now(timezone.utc)
        for key, ev in list(self._json_events.items()):
            at = datetime.fromisoformat(ev["event_at_utc"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            pending = any(
                t.get("event_id") == ev["id"] and t.get("status") == "pending"
                for t in self._json_triggers.values()
            )
            if not pending and at + timedelta(hours=1) < now:
                del self._json_events[key]
                removed += 1
        return removed

    def count_triggers_fired_since(self, since: datetime) -> int:
        if self._store.available:
            return self._store.count_calendar_triggers_fired_since(since)
        return sum(
            1
            for t in self._json_triggers.values()
            if t.get("status") == "fired"
            and t.get("fired_at")
            and datetime.fromisoformat(t["fired_at"]) >= since
        )

    def mark_trigger_fired_and_delete(self, trigger_id: str) -> None:
        if not self._store.available:
            row = self._json_triggers.get(trigger_id)
            if row:
                row["status"] = "fired"
                row["fired_at"] = datetime.now(timezone.utc).isoformat()
        self.delete_trigger(trigger_id)

    def next_pending_trigger_at(self) -> Optional[datetime]:
        if self._store.available:
            return self._store.next_calendar_trigger_at()
        times: list[datetime] = []
        now = datetime.now(timezone.utc)
        for row in self._json_triggers.values():
            if row.get("status") != "pending":
                continue
            at = datetime.fromisoformat(row["trigger_at_utc"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at > now:
                times.append(at)
        return min(times) if times else None

    def _row_to_trigger(self, row: dict) -> CalendarPollTrigger:
        at = datetime.fromisoformat(row["trigger_at_utc"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        fired = row.get("fired_at")
        fired_at = None
        if fired:
            fired_at = datetime.fromisoformat(fired)
            if fired_at.tzinfo is None:
                fired_at = fired_at.replace(tzinfo=timezone.utc)
        return CalendarPollTrigger(
            id=row["id"],
            event_id=row["event_id"],
            trigger_at_utc=at,
            offset_minutes=int(row["offset_minutes"]),
            source_ids=list(row.get("source_ids") or []),
            status=row.get("status", "pending"),
            fired_at=fired_at,
        )

    def _load_json_archive(self) -> None:
        if not CALENDAR_ARCHIVE.exists():
            return
        for line in CALENDAR_ARCHIVE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "event":
                key = f"{row.get('provider_id')}|{row.get('external_key')}"
                self._json_events[key] = row
            elif row.get("type") == "trigger" and row.get("status") == "pending":
                self._json_triggers[row["id"]] = row

    def _append_json(self, record: dict) -> None:
        CALENDAR_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        with CALENDAR_ARCHIVE.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
