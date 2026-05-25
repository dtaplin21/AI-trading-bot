"""Compute NewsFeatures for Feature Fusion and Risk agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from agents.news.economic_calendar_service import EconomicCalendarService
from agents.news.news_schemas import NewsEvent, NewsFeatures


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
        symbol_events = [
            e
            for e in recent_events
            if sym in [s.upper() for s in e.symbols_affected] or not e.symbols_affected
        ]

        if not symbol_events:
            blocked, reason = self._calendar.is_trading_blocked(sym)
            return NewsFeatures(
                symbol=sym,
                news_trading_blocked=blocked,
                news_size_reduction=self._calendar.get_size_reduction_factor(sym),
                news_requires_manual_approval=self._calendar.requires_manual_approval(sym),
                news_headline_summary=reason or "No recent news",
            )

        sentiment = sum(e.sentiment_score for e in symbol_events) / len(symbol_events)
        impact = max(e.impact_score for e in symbol_events)
        urgency = max(e.urgency_score for e in symbol_events)
        high_impact = sum(1 for e in symbol_events if e.impact_score >= 0.60)

        news_direction = 1 if sentiment > 0.15 else (-1 if sentiment < -0.15 else 0)
        alignment = 1.0 if technical_direction == 0 or news_direction == 0 else (
            1.0 if news_direction == technical_direction else -0.5
        )

        blocked, reason = self._calendar.is_trading_blocked(sym)
        size_red = self._calendar.get_size_reduction_factor(sym)
        manual = self._calendar.requires_manual_approval(sym)

        risk_penalty = 0.0
        if blocked:
            risk_penalty = 1.0
        elif size_red < 1.0:
            risk_penalty = 1.0 - size_red
        risk_penalty = max(risk_penalty, urgency * impact * 0.5)

        top = sorted(symbol_events, key=lambda e: e.impact_score, reverse=True)[:2]
        summary = "; ".join(e.headline[:60] for e in top)

        return NewsFeatures(
            symbol=sym,
            news_sentiment_score=sentiment,
            news_impact_score=impact,
            news_urgency_score=urgency,
            news_direction_alignment=alignment,
            news_risk_penalty=risk_penalty,
            news_event_count_2h=len(symbol_events),
            news_high_impact_count=high_impact,
            news_trading_blocked=blocked,
            news_size_reduction=size_red,
            news_requires_manual_approval=manual,
            news_headline_summary=summary or reason,
        )
