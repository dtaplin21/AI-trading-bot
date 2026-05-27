"""
agents/news/news_risk_filter_service.py

Converts the current news state into probability-adjusted risk features.
This is the bridge between raw news events and the Risk Agent's final decision.

Key principle: News does not directly block trades.
It adjusts probabilities and provides the Risk Agent with a structured
NewsRiskAssessment. The Risk Agent decides the final action.

The filter compares multiple news signals simultaneously:
- Recent high-impact events
- Scheduled upcoming events
- Breaking news flags
- Symbol-specific impact
- News conflict with technical setup
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_schemas import NewsEvent, NewsFeatures, NewsMode

logger = logging.getLogger(__name__)

HIGH_IMPACT_THRESHOLD = 0.65
BREAKING_URGENCY_THRESH = 0.85
VOLATILITY_REDUCE_THRESH = 0.55
CONFLICT_THRESHOLD = 0.40
NEWS_RECENCY_MINUTES = 120


class NewsRiskFilterService:
    """
    Produces a NewsFeatures block that the Feature Fusion Agent
    injects directly into FusedFeatureSet.
    """

    def __init__(self, calendar: EconomicCalendarService) -> None:
        self._calendar = calendar

    def compute_features(
        self,
        symbol: str,
        recent_events: list[NewsEvent],
        technical_direction: int = 0,
        at: Optional[datetime] = None,
    ) -> NewsFeatures:
        now = at or datetime.now(tz=timezone.utc)
        symbol_events = self._filter_symbol_events(symbol, recent_events, now)

        sentiment_score = self._aggregate_sentiment(symbol_events, now)
        impact_score = self._aggregate_impact(symbol_events, now)
        urgency_score = self._aggregate_urgency(symbol_events, now)
        volatility_score = self._aggregate_volatility(symbol_events, now)

        minutes_since = self._minutes_since_last_news(symbol_events, now)
        minutes_until = self._calendar.minutes_until_next_event(symbol)

        high_impact_active = impact_score >= HIGH_IMPACT_THRESHOLD
        breaking_active = urgency_score >= BREAKING_URGENCY_THRESH and any(
            e.news_mode == NewsMode.RISK_EVENT for e in symbol_events
        )
        symbol_match = len(symbol_events) > 0

        conflict_score = self._compute_conflict(sentiment_score, technical_direction)

        blocked, block_reason = self._calendar.is_trading_blocked(symbol, now)
        size_factor = self._calendar.get_size_reduction_factor(symbol, now)
        manual_required = self._calendar.requires_manual_approval(symbol, now)

        if not blocked and high_impact_active:
            blocked = True
            block_reason = "High-impact news active — news risk filter"

        reduce_size = size_factor < 1.0 or volatility_score >= VOLATILITY_REDUCE_THRESH

        latest = symbol_events[0] if symbol_events else None

        features = NewsFeatures(
            news_sentiment_score=round(sentiment_score, 3),
            news_impact_score=round(impact_score, 3),
            news_urgency_score=round(urgency_score, 3),
            volatility_risk_score=round(volatility_score, 3),
            minutes_since_last_news=round(minutes_since, 1),
            minutes_until_next_event=round(minutes_until, 1),
            high_impact_news_active=high_impact_active,
            breaking_news_active=breaking_active,
            affected_symbol_match=symbol_match,
            news_conflict_score=round(conflict_score, 3),
            trading_blocked=blocked,
            reduce_size_recommended=reduce_size,
            manual_approval_required=manual_required,
            news_risk_reason=block_reason,
            latest_headline=latest.headline if latest else None,
            latest_event_type=latest.event_type.value if latest else "none",
            latest_sentiment_label=latest.sentiment_label.value if latest else "neutral",
        )

        self._log_assessment(symbol, features)
        return features

    def _filter_symbol_events(
        self,
        symbol: str,
        events: list[NewsEvent],
        now: datetime,
    ) -> list[NewsEvent]:
        cutoff = now - timedelta(minutes=NEWS_RECENCY_MINUTES)
        filtered = []
        sym_upper = symbol.upper()
        for e in events:
            published = (
                e.published_at
                if e.published_at.tzinfo
                else e.published_at.replace(tzinfo=timezone.utc)
            )
            if published < cutoff:
                continue
            if sym_upper in [s.upper() for s in e.symbols_affected] or e.news_mode == NewsMode.RISK_EVENT:
                filtered.append(e)
        filtered.sort(key=lambda e: e.published_at, reverse=True)
        return filtered

    def _time_weight(self, event: NewsEvent, now: datetime) -> float:
        published = (
            event.published_at
            if event.published_at.tzinfo
            else event.published_at.replace(tzinfo=timezone.utc)
        )
        minutes_old = max(0.0, (now - published).total_seconds() / 60.0)
        return max(0.0, 1.0 - (minutes_old / NEWS_RECENCY_MINUTES))

    def _aggregate_sentiment(self, events: list[NewsEvent], now: datetime) -> float:
        if not events:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for e in events:
            w = self._time_weight(e, now) * e.impact_score
            weighted_sum += e.sentiment_score * w
            total_weight += w
        return (weighted_sum / total_weight) if total_weight > 0 else 0.0

    def _aggregate_impact(self, events: list[NewsEvent], now: datetime) -> float:
        if not events:
            return 0.0
        weighted = [(e.impact_score * self._time_weight(e, now), e) for e in events]
        weighted.sort(reverse=True)
        primary_score = weighted[0][0]
        secondary_score = (
            sum(w for w, _ in weighted[1:]) / max(1, len(weighted) - 1)
            if len(weighted) > 1
            else 0.0
        )
        return min(1.0, primary_score * 0.70 + secondary_score * 0.30)

    def _aggregate_urgency(self, events: list[NewsEvent], now: datetime) -> float:
        if not events:
            return 0.0
        return max(e.urgency_score * self._time_weight(e, now) for e in events)

    def _aggregate_volatility(self, events: list[NewsEvent], now: datetime) -> float:
        if not events:
            return 0.0
        return max(e.volatility_score * self._time_weight(e, now) for e in events)

    def _minutes_since_last_news(self, events: list[NewsEvent], now: datetime) -> float:
        if not events:
            return 9999.0
        latest = events[0].published_at
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return max(0.0, (now - latest).total_seconds() / 60.0)

    def _compute_conflict(self, news_sentiment: float, technical_direction: int) -> float:
        if technical_direction == 0 or abs(news_sentiment) < 0.20:
            return 0.0
        alignment = news_sentiment * technical_direction
        if alignment >= 0:
            return 0.0
        return min(1.0, abs(alignment))

    def _log_assessment(self, symbol: str, features: NewsFeatures) -> None:
        if features.trading_blocked:
            logger.warning(
                "NEWS RISK [%s]: BLOCKED — %s | impact=%.2f urgency=%.2f vol=%.2f",
                symbol,
                features.news_risk_reason,
                features.news_impact_score,
                features.news_urgency_score,
                features.volatility_risk_score,
            )
        elif features.high_impact_news_active:
            headline = features.latest_headline[:60] if features.latest_headline else "N/A"
            logger.info(
                "NEWS RISK [%s]: HIGH IMPACT ACTIVE | impact=%.2f | '%s'",
                symbol,
                features.news_impact_score,
                headline,
            )
