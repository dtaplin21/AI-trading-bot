"""
agents/news/news_sentiment_service.py

Classifies every raw news item into structured scores.
Two-layer approach:
  1. Fast rule-based scorer — keyword matching, event type detection
  2. LLM classifier — for ambiguous or breaking news items

The rule engine runs first and handles 80% of cases instantly.
The LLM only runs when the rule engine confidence is below threshold
or when the item is flagged as potentially high-impact.

Outputs: sentiment_score, impact_score, urgency_score, volatility_score,
         event_type, news_mode, sentiment_label
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.news.news_schemas import (
    EventType,
    ImpactLevel,
    NewsEvent,
    NewsMode,
    RawNewsItem,
    SentimentLabel,
    VolatilityRisk,
)

from llm.anthropic_client import AnthropicClient, get_anthropic_client

logger = logging.getLogger(__name__)

# ─── Keyword maps ─────────────────────────────────────────────────────────────

EVENT_KEYWORDS: list[tuple[EventType, list[str]]] = [
    (EventType.FOMC, ["fomc", "federal reserve", "fed meeting", "rate decision", "fed funds rate", "interest rate decision"]),
    (EventType.FED_SPEECH, ["fed chair", "powell", "fed president", "fed governor", "federal reserve speech", "fed remarks"]),
    (EventType.FED_POLICY, ["federal reserve", "monetary policy", "quantitative tightening", "qt", "qe", "rate hike", "rate cut", "fed pivot"]),
    (EventType.CPI, ["cpi", "consumer price index", "inflation data", "core inflation", "headline inflation"]),
    (EventType.PPI, ["ppi", "producer price index", "wholesale inflation"]),
    (EventType.NFP, ["nonfarm payrolls", "nfp", "jobs report", "employment report", "payroll"]),
    (EventType.GDP, ["gdp", "gross domestic product", "economic growth", "economic output"]),
    (EventType.JOBLESS_CLAIMS, ["jobless claims", "unemployment claims", "initial claims", "continuing claims"]),
    (EventType.OIL_INVENTORY, ["crude inventory", "oil inventory", "eia petroleum", "oil stocks", "crude stockpile"]),
    (EventType.TREASURY_YIELD, ["treasury yield", "10-year yield", "bond yield", "2-year yield", "yield curve", "inversion"]),
    (EventType.EARNINGS, ["earnings", "quarterly results", "eps", "revenue beat", "revenue miss", "guidance"]),
    (EventType.GEOPOLITICAL, ["war", "conflict", "sanctions", "geopolitical", "tariff", "trade war", "embargo"]),
    (EventType.CONTRACT_EXPIRY, ["contract expiry", "expiration", "rollover", "futures roll"]),
    (EventType.BREAKING, ["breaking:", "breaking news", "alert:", "urgent:"]),
    (EventType.INFLATION, ["inflation", "deflation", "stagflation", "price pressure"]),
    (EventType.EMPLOYMENT, ["unemployment", "labor market", "wage growth", "job openings", "jolts"]),
    (EventType.ANALYST, ["upgrades", "downgrades", "price target", "analyst", "overweight", "underweight"]),
]

SENTIMENT_KEYWORDS: list[tuple[float, list[str]]] = [
    (+0.9, ["beats expectations", "record high", "surges", "rallies", "strong growth", "blowout"]),
    (+0.7, ["above forecast", "higher than expected", "beat", "strong", "bullish", "upgrade", "buy rating"]),
    (+0.5, ["positive", "optimistic", "improving", "recovery", "growth"]),
    (+0.3, ["stable", "steady", "in line", "as expected"]),
    (0.0, ["unchanged", "flat", "mixed", "uncertain"]),
    (-0.3, ["cautious", "slowing", "below trend", "concern"]),
    (-0.5, ["negative", "pessimistic", "weak", "declining", "miss"]),
    (-0.7, ["below forecast", "lower than expected", "misses", "disappoints", "downgrade", "sell rating"]),
    (-0.9, ["crashes", "collapses", "plunges", "worst", "crisis", "emergency", "recession confirmed"]),
]

HIGH_IMPACT_PHRASES = [
    "fomc", "rate decision", "nonfarm payrolls", "cpi release", "ppi release",
    "gdp report", "emergency", "breaking", "fed chair", "powell speaks",
    "rate hike", "rate cut", "war", "sanctions", "default", "shutdown",
    "oil inventory", "eia report", "treasury yields spike",
]

HIGH_VOL_PHRASES = [
    "surprise", "unexpected", "shock", "unprecedented", "crisis",
    "emergency", "breaking", "flash crash", "circuit breaker",
    "margin call", "liquidity", "contagion", "bank failure",
]

URGENCY_PHRASES = [
    "breaking", "alert", "urgent", "just in", "minutes ago", "live",
    "now", "developing", "flash", "sudden",
]


class NewsSentimentService:
    """
    Scores every RawNewsItem and returns a populated NewsEvent.
    Rule engine first — LLM only for ambiguous or high-impact items.
    """

    def __init__(
        self,
        use_llm: bool = True,
        client: AnthropicClient | None = None,
    ) -> None:
        self._client = client or get_anthropic_client()
        self._use_llm = use_llm and self._client.is_configured

    async def classify(self, item: RawNewsItem) -> NewsEvent:
        """Full classification pipeline for one news item."""
        text = f"{item.headline} {item.summary or ''}".lower()

        event_type = self._detect_event_type(text)
        sentiment = self._score_sentiment(text)
        impact = self._score_impact(text, event_type)
        urgency = self._score_urgency(text, item.published_at)
        volatility = self._score_volatility(text, impact)
        sentiment_lbl = self._sentiment_label(sentiment)
        vol_risk = self._volatility_risk(volatility)
        news_mode = self._classify_mode(impact, event_type)
        impact_level = self._impact_level(impact)
        trade_action = self._determine_trade_action(impact, volatility, event_type, news_mode)

        explanation = ""
        if self._use_llm and (impact > 0.65 or event_type == EventType.BREAKING):
            try:
                explanation = await self._llm_explain(item.headline, item.summary, event_type)
            except Exception as e:
                logger.warning("LLM classification failed: %s", e)
                explanation = f"Auto-classified as {event_type.value}"
        else:
            explanation = f"Rule-classified as {event_type.value} | sentiment: {sentiment_lbl.value}"

        return NewsEvent(
            source=item.source,
            headline=item.headline,
            summary=item.summary,
            url=item.url,
            published_at=item.published_at,
            event_type=event_type,
            news_mode=news_mode,
            sentiment_score=round(sentiment, 3),
            impact_score=round(impact, 3),
            urgency_score=round(urgency, 3),
            volatility_score=round(volatility, 3),
            sentiment_label=sentiment_lbl,
            volatility_risk=vol_risk,
            impact_level=impact_level,
            trade_action=trade_action,
            explanation=explanation,
        )

    async def classify_batch(self, items: list[RawNewsItem]) -> list[NewsEvent]:
        """Classify a batch of raw items. Returns NewsEvent list."""
        tasks = [self.classify(item) for item in items]
        return await asyncio.gather(*tasks, return_exceptions=False)

    def _detect_event_type(self, text: str) -> EventType:
        for event_type, keywords in EVENT_KEYWORDS:
            if any(kw in text for kw in keywords):
                return event_type
        return EventType.GENERAL_MARKET

    def _score_sentiment(self, text: str) -> float:
        scores = []
        weights = []
        for score, keywords in SENTIMENT_KEYWORDS:
            for kw in keywords:
                if kw in text:
                    scores.append(score)
                    weights.append(abs(score) + 0.1)

        if not scores:
            return 0.0

        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return max(-1.0, min(1.0, weighted_sum / total_weight))

    def _score_impact(self, text: str, event_type: EventType) -> float:
        base_scores = {
            EventType.FOMC: 0.90,
            EventType.CPI: 0.88,
            EventType.NFP: 0.85,
            EventType.PPI: 0.80,
            EventType.GDP: 0.78,
            EventType.FED_SPEECH: 0.75,
            EventType.FED_POLICY: 0.80,
            EventType.OIL_INVENTORY: 0.65,
            EventType.TREASURY_YIELD: 0.70,
            EventType.JOBLESS_CLAIMS: 0.65,
            EventType.EARNINGS: 0.60,
            EventType.GEOPOLITICAL: 0.75,
            EventType.BREAKING: 0.85,
            EventType.CONTRACT_EXPIRY: 0.50,
            EventType.INFLATION: 0.70,
            EventType.EMPLOYMENT: 0.65,
            EventType.ANALYST: 0.25,
            EventType.GENERAL_MARKET: 0.20,
            EventType.UNKNOWN: 0.15,
        }
        score = base_scores.get(event_type, 0.20)
        phrase_boost = sum(0.04 for p in HIGH_IMPACT_PHRASES if p in text)
        return min(1.0, score + phrase_boost)

    def _score_urgency(self, text: str, published_at: datetime) -> float:
        now = datetime.now(tz=timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        minutes_old = max(0.0, (now - published_at).total_seconds() / 60.0)

        if minutes_old <= 5:
            time_score = 1.0
        elif minutes_old <= 30:
            time_score = 1.0 - ((minutes_old - 5) / 25) * 0.4
        elif minutes_old <= 120:
            time_score = 0.6 - ((minutes_old - 30) / 90) * 0.3
        else:
            time_score = max(0.0, 0.3 - (minutes_old - 120) / 1320 * 0.3)

        keyword_boost = sum(0.05 for p in URGENCY_PHRASES if p in text)
        return min(1.0, time_score + keyword_boost)

    def _score_volatility(self, text: str, impact: float) -> float:
        base = impact * 0.7
        phrase_boost = sum(0.06 for p in HIGH_VOL_PHRASES if p in text)
        return min(1.0, base + phrase_boost)

    def _sentiment_label(self, score: float) -> SentimentLabel:
        if score > 0.25:
            return SentimentLabel.BULLISH
        if score < -0.25:
            return SentimentLabel.BEARISH
        if abs(score) < 0.1:
            return SentimentLabel.NEUTRAL
        return SentimentLabel.MIXED

    def _volatility_risk(self, score: float) -> VolatilityRisk:
        if score >= 0.80:
            return VolatilityRisk.EXTREME
        if score >= 0.60:
            return VolatilityRisk.HIGH
        if score >= 0.35:
            return VolatilityRisk.MEDIUM
        return VolatilityRisk.LOW

    def _classify_mode(self, impact: float, event_type: EventType) -> NewsMode:
        risk_events = {
            EventType.FOMC,
            EventType.CPI,
            EventType.NFP,
            EventType.PPI,
            EventType.GDP,
            EventType.BREAKING,
            EventType.GEOPOLITICAL,
            EventType.FED_POLICY,
            EventType.FED_SPEECH,
        }
        if impact >= 0.70 or event_type in risk_events:
            return NewsMode.RISK_EVENT
        if impact >= 0.35:
            return NewsMode.CONTEXTUAL
        return NewsMode.INFORMATIONAL

    def _impact_level(self, score: float) -> ImpactLevel:
        if score >= 0.80:
            return ImpactLevel.CRITICAL
        if score >= 0.60:
            return ImpactLevel.HIGH
        if score >= 0.35:
            return ImpactLevel.MEDIUM
        return ImpactLevel.LOW

    def _determine_trade_action(
        self,
        impact: float,
        volatility: float,
        event_type: EventType,
        mode: NewsMode,
    ) -> str:
        if mode == NewsMode.RISK_EVENT and impact >= 0.80:
            return "block"
        if mode == NewsMode.RISK_EVENT and impact >= 0.65:
            return "risk_filter"
        if volatility >= 0.70:
            return "reduce_size"
        if mode == NewsMode.CONTEXTUAL:
            return "context_only"
        return "none"

    async def _llm_explain(
        self,
        headline: str,
        summary: Optional[str],
        event_type: EventType,
    ) -> str:
        prompt = f"""You are a market analyst for a futures trading AI.

Headline: {headline}
Summary: {summary or 'No summary available'}
Detected event type: {event_type.value}

In 2-3 sentences, explain:
1. What this event means for futures markets (ES, NQ, CL, GC)
2. Whether it is bullish, bearish, or mixed
3. Whether trading should be paused, reduced, or can continue normally

Be direct and specific. No preamble."""

        return await self._client.complete(user=prompt, max_tokens=200, temperature=0.3)
