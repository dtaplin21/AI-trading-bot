"""Calendar event and poll-trigger models (all times UTC)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from agents.news.news_schemas import EventType, ImpactLevel


class CalendarEventDraft(BaseModel):
    """Normalized event from any calendar provider before persistence."""

    provider_id: str
    external_key: str
    event_name: str
    event_type: EventType
    event_at_utc: datetime
    impact_level: ImpactLevel
    source_ids: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)
    country: str = "US"


class CalendarPollTrigger(BaseModel):
    """One scheduled fetch at event_at + offset_minutes for specific sources."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str = ""
    trigger_at_utc: datetime
    offset_minutes: int
    source_ids: list[str] = Field(default_factory=list)
    status: str = "pending"
    fired_at: Optional[datetime] = None
