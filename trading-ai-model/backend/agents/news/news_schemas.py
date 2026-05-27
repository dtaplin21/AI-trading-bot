"""
agents/news/news_schemas.py

All Pydantic v2 schemas for the Market News Intelligence Agent.
These integrate directly into FusedFeatureSet via the NewsFeatures block.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Enums ────────────────────────────────────────────────────────────────────

class NewsSource(str, Enum):
    FINNHUB = "finnhub"
    BENZINGA = "benzinga"
    POLYGON = "polygon"
    FMP = "financial_modeling_prep"
    NEWSAPI = "newsapi"
    MARKETAUX = "marketaux"
    ALPHA_VANTAGE = "alpha_vantage"
    RSS = "rss"
    FRED = "fred"
    BLS = "bls"
    FEDERAL_RESERVE = "federal_reserve"
    EIA = "eia"
    TREASURY = "treasury"


class EventType(str, Enum):
    CPI = "cpi"
    PPI = "ppi"
    FOMC = "fomc"
    NFP = "nonfarm_payrolls"
    GDP = "gdp"
    FED_SPEECH = "fed_speech"
    JOBLESS_CLAIMS = "jobless_claims"
    OIL_INVENTORY = "oil_inventory"
    TREASURY_YIELD = "treasury_yield"
    EARNINGS = "earnings"
    GEOPOLITICAL = "geopolitical"
    FED_POLICY = "fed_policy"
    INFLATION = "inflation"
    EMPLOYMENT = "employment"
    GENERAL_MARKET = "general_market"
    CONTRACT_EXPIRY = "contract_expiry"
    BREAKING = "breaking"
    ANALYST = "analyst"
    UNKNOWN = "unknown"


class SentimentLabel(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class VolatilityRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class NewsMode(str, Enum):
    INFORMATIONAL = "informational"
    CONTEXTUAL = "contextual"
    RISK_EVENT = "risk_event"


class ImpactLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ─── Raw ingested article ──────────────────────────────────────────────────────

class RawNewsItem(BaseModel):
    """Raw item as it arrives from any source before processing."""
    source: NewsSource
    headline: str
    summary: Optional[str] = None
    url: Optional[str] = None
    published_at: datetime
    raw_payload: dict = Field(default_factory=dict)


# ─── Processed news event ─────────────────────────────────────────────────────

class NewsEvent(BaseModel):
    """Fully processed news event. Stored in news_events table."""
    id: Optional[str] = None
    source: NewsSource
    headline: str
    summary: Optional[str] = None
    url: Optional[str] = None
    published_at: datetime
    created_at: datetime = Field(default_factory=_utc_now)

    event_type: EventType = EventType.UNKNOWN
    news_mode: NewsMode = NewsMode.INFORMATIONAL

    symbols_affected: list[str] = Field(default_factory=list)
    asset_classes: list[str] = Field(default_factory=list)

    sentiment_score: float = 0.0
    impact_score: float = 0.0
    urgency_score: float = 0.0
    volatility_score: float = 0.0

    sentiment_label: SentimentLabel = SentimentLabel.NEUTRAL
    volatility_risk: VolatilityRisk = VolatilityRisk.LOW
    impact_level: ImpactLevel = ImpactLevel.LOW

    trade_action: str = "none"
    explanation: str = ""


# ─── Economic calendar event ──────────────────────────────────────────────────

class EconomicEvent(BaseModel):
    """Scheduled macro event from economic calendar."""
    id: Optional[str] = None
    event_name: str
    event_type: EventType
    scheduled_at: datetime
    actual_value: Optional[float] = None
    forecast_value: Optional[float] = None
    previous_value: Optional[float] = None
    surprise_pct: Optional[float] = None
    impact_level: ImpactLevel = ImpactLevel.LOW
    affected_symbols: list[str] = Field(default_factory=list)
    country: str = "US"
    source: NewsSource = NewsSource.FRED
    created_at: datetime = Field(default_factory=_utc_now)


# ─── News risk window ──────────────────────────────────────────────────────────

class NewsRiskWindow(BaseModel):
    """Active trading restriction window around a news event."""
    id: Optional[str] = None
    event_name: str
    event_type: EventType
    starts_at: datetime
    ends_at: datetime
    affected_symbols: list[str] = Field(default_factory=list)
    risk_level: VolatilityRisk = VolatilityRisk.LOW
    trading_allowed: bool = True
    reduce_size: bool = False
    require_manual: bool = False
    reason: str = ""
    created_at: datetime = Field(default_factory=_utc_now)


# ─── Symbol news impact ────────────────────────────────────────────────────────

class SymbolNewsImpact(BaseModel):
    """Per-symbol impact record."""
    id: Optional[str] = None
    news_event_id: str
    symbol: str
    impact_direction: int = 0
    confidence: float = 0.0
    created_at: datetime = Field(default_factory=_utc_now)


# ─── News features fed to FeatureFusion ───────────────────────────────────────

class NewsFeatures(BaseModel):
    """Structured feature block for Feature Fusion Agent."""
    news_sentiment_score: float = 0.0
    news_impact_score: float = 0.0
    news_urgency_score: float = 0.0
    volatility_risk_score: float = 0.0

    minutes_since_last_news: float = 9999.0
    minutes_until_next_event: float = 9999.0

    high_impact_news_active: bool = False
    breaking_news_active: bool = False
    affected_symbol_match: bool = False
    news_conflict_score: float = 0.0

    trading_blocked: bool = False
    reduce_size_recommended: bool = False
    manual_approval_required: bool = False
    news_risk_reason: str = ""

    latest_headline: Optional[str] = None
    latest_event_type: str = "none"
    latest_sentiment_label: str = "neutral"
