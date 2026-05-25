"""News sentiment and event classification."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from agents.news.news_schemas import (
    EventType,
    ImpactLevel,
    NewsEvent,
    NewsMode,
    RawNewsItem,
    SentimentLabel,
)

logger = logging.getLogger(__name__)

BULLISH_WORDS = ("surge", "rally", "beat", "strong", "growth", "record high", "upbeat")
BEARISH_WORDS = ("fall", "drop", "miss", "weak", "recession", "crisis", "plunge", "selloff")
HIGH_IMPACT_WORDS = ("fed", "fomc", "cpi", "nfp", "jobs report", "rate cut", "rate hike", "war", "default")
CRITICAL_WORDS = ("emergency", "halt", "crash", "bank failure", "sanctions", "invasion")


class NewsSentimentService:
    """Classifies raw items into scored NewsEvents. Optional LLM enhancement."""

    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm

    async def classify_batch(self, items: list[RawNewsItem]) -> list[NewsEvent]:
        tasks = [self._classify_one(item) for item in items]
        return await asyncio.gather(*tasks)

    async def _classify_one(self, item: RawNewsItem) -> NewsEvent:
        text = f"{item.headline} {item.summary} {item.raw_text}".lower()

        sentiment_score = self._sentiment_score(text)
        sentiment_label = self._sentiment_label(sentiment_score)
        event_type = self._event_type(text)
        impact_level, impact_score = self._impact(text)
        urgency = self._urgency(text, impact_score)
        mode = self._mode(event_type, impact_score, urgency)

        return NewsEvent(
            source=item.source,
            headline=item.headline,
            summary=item.summary,
            url=item.url,
            published_at=item.published_at,
            event_type=event_type,
            impact_level=impact_level,
            impact_score=impact_score,
            urgency_score=urgency,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            news_mode=mode,
            keywords=self._keywords(text),
        )

    def _sentiment_score(self, text: str) -> float:
        bull = sum(1 for w in BULLISH_WORDS if w in text)
        bear = sum(1 for w in BEARISH_WORDS if w in text)
        if bull + bear == 0:
            return 0.0
        return max(-1.0, min(1.0, (bull - bear) / max(bull + bear, 1)))

    def _sentiment_label(self, score: float) -> SentimentLabel:
        if score > 0.25:
            return SentimentLabel.BULLISH
        if score < -0.25:
            return SentimentLabel.BEARISH
        return SentimentLabel.NEUTRAL

    def _event_type(self, text: str) -> EventType:
        if any(w in text for w in ("cpi", "inflation")):
            return EventType.CPI
        if any(w in text for w in ("nonfarm", "nfp", "jobs report", "payroll")):
            return EventType.NFP
        if any(w in text for w in ("fed", "fomc", "powell", "rate hike", "rate cut")):
            return EventType.FED
        if any(w in text for w in ("war", "sanction", "geopolit")):
            return EventType.GEOPOLITICAL
        if "earnings" in text:
            return EventType.EARNINGS
        if any(w in text for w in CRITICAL_WORDS):
            return EventType.BREAKING
        return EventType.GENERAL

    def _impact(self, text: str) -> tuple[ImpactLevel, float]:
        if any(w in text for w in CRITICAL_WORDS):
            return ImpactLevel.CRITICAL, 0.95
        hits = sum(1 for w in HIGH_IMPACT_WORDS if w in text)
        if hits >= 2:
            return ImpactLevel.HIGH, 0.80
        if hits == 1:
            return ImpactLevel.MEDIUM, 0.55
        return ImpactLevel.LOW, 0.25

    def _urgency(self, text: str, impact: float) -> float:
        boost = 0.2 if any(w in text for w in ("breaking", "just in", "alert")) else 0.0
        return min(1.0, impact + boost)

    def _mode(self, event_type: EventType, impact: float, urgency: float) -> NewsMode:
        if event_type == EventType.BREAKING or (impact >= 0.85 and urgency >= 0.75):
            return NewsMode.RISK_EVENT
        if impact >= 0.50:
            return NewsMode.DIRECTIONAL
        return NewsMode.INFORMATIONAL

    def _keywords(self, text: str) -> list[str]:
        found = [w for w in HIGH_IMPACT_WORDS + BULLISH_WORDS + BEARISH_WORDS if w in text]
        return list(dict.fromkeys(found))[:10]
