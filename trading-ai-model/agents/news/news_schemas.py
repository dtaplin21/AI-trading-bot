"""News intelligence schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    MACRO = "macro"
    EARNINGS = "earnings"
    GEOPOLITICAL = "geopolitical"
    FED = "fed"
    CPI = "cpi"
    NFP = "nfp"
    BREAKING = "breaking"
    SECTOR = "sector"
    GENERAL = "general"


class ImpactLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SentimentLabel(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class NewsMode(str, Enum):
    INFORMATIONAL = "informational"
    DIRECTIONAL = "directional"
    RISK_EVENT = "risk_event"


class RawNewsItem(BaseModel):
    source: str
    headline: str
    summary: str = ""
    url: str = ""
    published_at: datetime
    raw_text: str = ""


class NewsEvent(BaseModel):
    id: str = ""
    source: str
    headline: str
    summary: str = ""
    url: str = ""
    published_at: datetime
    event_type: EventType = EventType.GENERAL
    impact_level: ImpactLevel = ImpactLevel.LOW
    impact_score: float = Field(0.0, ge=0.0, le=1.0)
    urgency_score: float = Field(0.0, ge=0.0, le=1.0)
    sentiment_score: float = Field(0.0, ge=-1.0, le=1.0)
    sentiment_label: SentimentLabel = SentimentLabel.NEUTRAL
    news_mode: NewsMode = NewsMode.INFORMATIONAL
    symbols_affected: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class SymbolNewsImpact(BaseModel):
    news_event_id: str = ""
    symbol: str
    impact_score: float = 0.0
    direction_bias: int = 0  # +1 bull, -1 bear, 0 neutral
    relevance: float = 0.0


class EconomicEvent(BaseModel):
    name: str
    scheduled_at: datetime
    impact: ImpactLevel = ImpactLevel.HIGH
    symbols: list[str] = Field(default_factory=list)
    block_minutes_before: int = 15
    block_minutes_after: int = 30
    size_reduction: float = 0.5


class NewsRiskWindow(BaseModel):
    symbol: str
    reason: str
    starts_at: datetime
    ends_at: datetime
    block_trading: bool = False
    size_reduction: float = 1.0
    requires_manual_approval: bool = False


class NewsFeatures(BaseModel):
    symbol: str
    news_sentiment_score: float = 0.0
    news_impact_score: float = 0.0
    news_urgency_score: float = 0.0
    news_direction_alignment: float = 0.0
    news_risk_penalty: float = 0.0
    news_event_count_2h: int = 0
    news_high_impact_count: int = 0
    news_trading_blocked: bool = False
    news_size_reduction: float = 1.0
    news_requires_manual_approval: bool = False
    news_headline_summary: str = ""
