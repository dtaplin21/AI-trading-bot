"""Rule-based calendar event classification — deterministic, not LLM."""

from __future__ import annotations

import re

from agents.news.calendar.schemas import CalendarEventDraft
from agents.news.news_schemas import EventType, ImpactLevel

# Releases that should never create poll triggers (noise on FRED calendar)
_IGNORE_PATTERNS = re.compile(
    r"coinbase|cryptocurren|credit card|moody'?s daily|commercial paper|"
    r"dow jones average|nasdaq daily|optimal blue|nikkei index|"
    r"economic news index|visa spending",
    re.I,
)

_MACRO_RULES: list[tuple[EventType, ImpactLevel, re.Pattern]] = [
    (EventType.FOMC, ImpactLevel.CRITICAL, re.compile(r"fomc|federal open market|fed funds rate decision", re.I)),
    (EventType.CPI, ImpactLevel.CRITICAL, re.compile(r"consumer price index|\bcpi\b", re.I)),
    (EventType.NFP, ImpactLevel.CRITICAL, re.compile(r"nonfarm payroll|non-farm payroll|\bnfp\b|employment situation", re.I)),
    (EventType.PPI, ImpactLevel.HIGH, re.compile(r"producer price index|\bppi\b", re.I)),
    (EventType.GDP, ImpactLevel.HIGH, re.compile(r"gross domestic product|\bgdp\b", re.I)),
    (EventType.JOBLESS_CLAIMS, ImpactLevel.HIGH, re.compile(r"jobless claims|unemployment insurance weekly", re.I)),
    (EventType.OIL_INVENTORY, ImpactLevel.HIGH, re.compile(r"eia petroleum|crude inventory|oil inventory", re.I)),
    (EventType.FED_SPEECH, ImpactLevel.HIGH, re.compile(r"fed chair|powell|federal reserve speech", re.I)),
    (EventType.TREASURY_YIELD, ImpactLevel.MEDIUM, re.compile(r"treasury yield|h\.15 selected interest", re.I)),
]

_DEFAULT_MACRO_SOURCES = [
    "finnhub_news",
    "rss_cnbc_markets",
    "rss_marketwatch",
    "rss_yahoo_finance",
]

_EVENT_SOURCES: dict[EventType, list[str]] = {
    EventType.OIL_INVENTORY: ["eia_petroleum", "finnhub_news", "rss_cnbc_markets"],
    EventType.FOMC: ["finnhub_news", "finnhub_calendar", "rss_cnbc_markets", "rss_marketwatch"],
    EventType.NFP: ["finnhub_news", "finnhub_calendar", "rss_cnbc_markets", "rss_marketwatch"],
    EventType.CPI: ["finnhub_news", "finnhub_calendar", "rss_cnbc_markets", "rss_marketwatch"],
}


def classify_event_name(name: str) -> tuple[EventType, ImpactLevel]:
    for event_type, impact, pattern in _MACRO_RULES:
        if pattern.search(name):
            return event_type, impact
    return EventType.GENERAL_MARKET, ImpactLevel.LOW


def default_sources_for(event_type: EventType) -> list[str]:
    return list(_EVENT_SOURCES.get(event_type, _DEFAULT_MACRO_SOURCES))


def should_schedule_polling(draft: CalendarEventDraft) -> bool:
    """Only HIGH/CRITICAL macro events get pinpoint triggers."""
    if _IGNORE_PATTERNS.search(draft.event_name):
        return False
    if draft.impact_level not in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}:
        return False
    if draft.event_type == EventType.GENERAL_MARKET:
        return False
    return True


def enrich_draft(draft: CalendarEventDraft) -> CalendarEventDraft:
    event_type, impact = classify_event_name(draft.event_name)
    sources = draft.source_ids or default_sources_for(event_type)
    return draft.model_copy(
        update={
            "event_type": event_type,
            "impact_level": max_impact(draft.impact_level, impact),
            "source_ids": sources,
        }
    )


def max_impact(a: ImpactLevel, b: ImpactLevel) -> ImpactLevel:
    order = [ImpactLevel.LOW, ImpactLevel.MEDIUM, ImpactLevel.HIGH, ImpactLevel.CRITICAL]
    return a if order.index(a) >= order.index(b) else b
