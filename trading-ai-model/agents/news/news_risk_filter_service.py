"""Compute NewsFeatures for Feature Fusion and Risk agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_schemas import EventType, NewsEvent, NewsFeatures, NewsMode


class NewsRiskFilterService:
    """Aggregates news + calendar into structured features."""

    def __init__(self, calendar: EconomicCalendarService):
        self._calendar = calendar

    def compute_features(
        self,
        symbol: str,
        recent_events: list[NewsEvent],
        technical_direction: int = 0,
        at: Optional[datetime] = None,
    ) -> NewsFeatures:
        sym = symbol.upper()
        now = at or datetime.now(timezone.utc)
        symbol_events = [
            e
            for e in recent_events
            if sym in [s.upper() for s in e.symbols_affected]
            or (not e.symbols_affected and e.news_mode == NewsMode.RISK_EVENT)
        ]

        blocked, block_reason = self._calendar.is_trading_blocked(sym)
        size_red = self._calendar.get_size_reduction_factor(sym)
        manual = self._calendar.requires_manual_approval(sym)
        minutes_until = self._calendar.minutes_until_next_event(sym)

        if not symbol_events:
            return NewsFeatures(
                news_trading_blocked=blocked,
                reduce_size_recommended=size_red < 1.0,
                manual_approval_required=manual,
                news_risk_reason=block_reason or "No recent news",
                minutes_until_next_event=minutes_until,
                affected_symbol_match=False,
            )

        latest = max(symbol_events, key=lambda e: e.published_at)
        sentiment = sum(e.sentiment_score for e in symbol_events) / len(symbol_events)
        impact = max(e.impact_score for e in symbol_events)
        urgency = max(e.urgency_score for e in symbol_events)
        volatility = max(e.volatility_score for e in symbol_events)

        published = latest.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        minutes_since = (now - published).total_seconds() / 60

        news_direction = 1 if sentiment > 0.15 else (-1 if sentiment < -0.15 else 0)
        conflict = 0.0
        if technical_direction != 0 and news_direction != 0 and news_direction != technical_direction:
            conflict = min(1.0, abs(sentiment) * impact)

        high_impact = any(e.impact_score >= 0.60 for e in symbol_events)
        breaking = any(e.news_mode == NewsMode.RISK_EVENT for e in symbol_events)

        if latest.trade_action == "block":
            blocked = True
            block_reason = block_reason or latest.headline[:80]
        elif latest.trade_action in ("manual_approval", "risk_filter"):
            manual = True

        return NewsFeatures(
            news_sentiment_score=sentiment,
            news_impact_score=impact,
            news_urgency_score=urgency,
            volatility_risk_score=volatility,
            minutes_since_last_news=minutes_since,
            minutes_until_next_event=minutes_until,
            high_impact_news_active=high_impact,
            breaking_news_active=breaking,
            affected_symbol_match=True,
            news_conflict_score=conflict,
            trading_blocked=blocked,
            reduce_size_recommended=size_red < 1.0 or latest.trade_action == "reduce_size",
            manual_approval_required=manual,
            news_risk_reason=block_reason or latest.explanation,
            latest_headline=latest.headline,
            latest_event_type=latest.event_type.value,
            latest_sentiment_label=latest.sentiment_label.value,
        )
