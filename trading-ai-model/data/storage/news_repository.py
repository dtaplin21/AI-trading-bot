"""News intelligence persistence — DDL helpers and row mappers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from agents.news.news_schemas import (
    EconomicEvent,
    EventType,
    ImpactLevel,
    NewsEvent,
    NewsFeatures,
    NewsMode,
    NewsRiskWindow,
    NewsSource,
    SentimentLabel,
    VolatilityRisk,
)

NEWS_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS economic_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name       VARCHAR(256)  NOT NULL,
    event_type       VARCHAR(64)   NOT NULL,
    scheduled_at     TIMESTAMPTZ   NOT NULL,
    country          VARCHAR(8)    NOT NULL DEFAULT 'US',
    impact_level     VARCHAR(16)   NOT NULL DEFAULT 'medium',
    source           VARCHAR(64)   NOT NULL DEFAULT 'fmp',
    forecast_value   NUMERIC(18,6),
    actual_value     NUMERIC(18,6),
    previous_value   NUMERIC(18,6),
    surprise_pct     NUMERIC(8,4),
    affected_symbols TEXT[],
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS news_risk_windows (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name       VARCHAR(256)  NOT NULL,
    event_type       VARCHAR(64)   NOT NULL,
    starts_at        TIMESTAMPTZ   NOT NULL,
    ends_at          TIMESTAMPTZ   NOT NULL,
    affected_symbols TEXT[],
    risk_level       VARCHAR(16)   NOT NULL DEFAULT 'low',
    trading_allowed  BOOLEAN       NOT NULL DEFAULT TRUE,
    reduce_size      BOOLEAN       NOT NULL DEFAULT FALSE,
    require_manual   BOOLEAN       NOT NULL DEFAULT FALSE,
    reason           TEXT,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS symbol_news_impact (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    news_event_id    TEXT NOT NULL,
    symbol           VARCHAR(16) NOT NULL,
    impact_direction SMALLINT NOT NULL DEFAULT 0,
    confidence       NUMERIC(6,4) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (news_event_id, symbol)
);

CREATE TABLE IF NOT EXISTS news_feature_snapshots (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id                UUID,
    symbol                   VARCHAR(16) NOT NULL,
    timeframe                VARCHAR(8)  NOT NULL,
    snapshot_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    news_sentiment_score     NUMERIC(6,4) NOT NULL DEFAULT 0,
    news_impact_score        NUMERIC(6,4) NOT NULL DEFAULT 0,
    news_urgency_score       NUMERIC(6,4) NOT NULL DEFAULT 0,
    volatility_risk_score    NUMERIC(6,4) NOT NULL DEFAULT 0,
    minutes_since_last_news  NUMERIC(8,2) NOT NULL DEFAULT 9999,
    minutes_until_next_event NUMERIC(8,2) NOT NULL DEFAULT 9999,
    high_impact_news_active  BOOLEAN NOT NULL DEFAULT FALSE,
    breaking_news_active     BOOLEAN NOT NULL DEFAULT FALSE,
    affected_symbol_match    BOOLEAN NOT NULL DEFAULT FALSE,
    news_conflict_score      NUMERIC(6,4) NOT NULL DEFAULT 0,
    trading_blocked          BOOLEAN NOT NULL DEFAULT FALSE,
    reduce_size_recommended  BOOLEAN NOT NULL DEFAULT FALSE,
    manual_approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    news_risk_reason         TEXT,
    latest_headline          TEXT,
    latest_event_type        VARCHAR(64),
    latest_sentiment_label   VARCHAR(16)
);

CREATE TABLE IF NOT EXISTS news_sentiment_scores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(16) NOT NULL,
    window_minutes      INTEGER NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    avg_sentiment       NUMERIC(6,4) NOT NULL DEFAULT 0,
    avg_impact          NUMERIC(6,4) NOT NULL DEFAULT 0,
    max_urgency         NUMERIC(6,4) NOT NULL DEFAULT 0,
    max_volatility      NUMERIC(6,4) NOT NULL DEFAULT 0,
    event_count         INTEGER NOT NULL DEFAULT 0,
    high_impact_count   INTEGER NOT NULL DEFAULT 0,
    dominant_sentiment  VARCHAR(16),
    dominant_event_type VARCHAR(64)
);
"""

NEWS_EVENTS_V2_COLUMNS = """
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS volatility_score NUMERIC(6,4) NOT NULL DEFAULT 0;
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS volatility_risk VARCHAR(16) NOT NULL DEFAULT 'low';
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS trade_action VARCHAR(32) NOT NULL DEFAULT 'none';
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS explanation TEXT;
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS asset_classes TEXT[];
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS raw_payload JSONB NOT NULL DEFAULT '{}';
"""


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def row_to_risk_window(row: dict[str, Any]) -> NewsRiskWindow:
    return NewsRiskWindow(
        id=str(row["id"]) if row.get("id") else None,
        event_name=row["event_name"],
        event_type=EventType(row["event_type"]),
        starts_at=_aware(row["starts_at"]),
        ends_at=_aware(row["ends_at"]),
        affected_symbols=list(row.get("affected_symbols") or []),
        risk_level=VolatilityRisk(row.get("risk_level") or "low"),
        trading_allowed=bool(row.get("trading_allowed", True)),
        reduce_size=bool(row.get("reduce_size", False)),
        require_manual=bool(row.get("require_manual", False)),
        reason=row.get("reason") or "",
        created_at=_aware(row.get("created_at") or datetime.now(timezone.utc)),
    )


def _parse_source(val: str | None) -> NewsSource:
    if not val:
        return NewsSource.FMP
    try:
        return NewsSource(val)
    except ValueError:
        aliases = {"fmp": NewsSource.FMP, "fred": NewsSource.FRED}
        return aliases.get(val.lower(), NewsSource.FRED)


def row_to_economic_event(row: dict[str, Any]) -> EconomicEvent:
    return EconomicEvent(
        id=str(row["id"]) if row.get("id") else None,
        event_name=row["event_name"],
        event_type=EventType(row["event_type"]),
        scheduled_at=_aware(row["scheduled_at"]),
        actual_value=float(row["actual_value"]) if row.get("actual_value") is not None else None,
        forecast_value=float(row["forecast_value"]) if row.get("forecast_value") is not None else None,
        previous_value=float(row["previous_value"]) if row.get("previous_value") is not None else None,
        surprise_pct=float(row["surprise_pct"]) if row.get("surprise_pct") is not None else None,
        impact_level=ImpactLevel(row.get("impact_level") or "medium"),
        affected_symbols=list(row.get("affected_symbols") or []),
        country=row.get("country") or "US",
        source=_parse_source(row.get("source")),
        created_at=_aware(row.get("created_at") or datetime.now(timezone.utc)),
    )


def economic_event_row(event: EconomicEvent) -> tuple:
    scheduled = _aware(event.scheduled_at)
    eid = event.id or str(uuid.uuid4())
    return (
        eid,
        event.event_name,
        event.event_type.value,
        scheduled,
        event.country,
        event.impact_level.value,
        event.source.value if hasattr(event.source, "value") else event.source,
        event.forecast_value,
        event.actual_value,
        event.previous_value,
        event.surprise_pct,
        event.affected_symbols or None,
    )


def risk_window_row(window: NewsRiskWindow) -> tuple:
    wid = window.id or str(uuid.uuid4())
    return (
        wid,
        window.event_name,
        window.event_type.value,
        _aware(window.starts_at),
        _aware(window.ends_at),
        window.affected_symbols or None,
        window.risk_level.value,
        window.trading_allowed,
        window.reduce_size,
        window.require_manual,
        window.reason,
    )


def news_features_row(
    features: NewsFeatures,
    symbol: str,
    timeframe: str,
    signal_id: Optional[str] = None,
) -> tuple:
    return (
        signal_id,
        symbol.upper(),
        timeframe,
        features.news_sentiment_score,
        features.news_impact_score,
        features.news_urgency_score,
        features.volatility_risk_score,
        features.minutes_since_last_news,
        features.minutes_until_next_event,
        features.high_impact_news_active,
        features.breaking_news_active,
        features.affected_symbol_match,
        features.news_conflict_score,
        features.trading_blocked,
        features.reduce_size_recommended,
        features.manual_approval_required,
        features.news_risk_reason,
        features.latest_headline,
        features.latest_event_type,
        features.latest_sentiment_label,
    )


def news_event_insert_row(event: NewsEvent) -> tuple:
    pub = _aware(event.published_at)
    created = _aware(event.created_at)
    return (
        event.id,
        event.source.value if hasattr(event.source, "value") else event.source,
        event.headline,
        event.summary,
        event.url,
        pub,
        created,
        event.event_type.value,
        event.news_mode.value,
        event.sentiment_score,
        event.impact_score,
        event.urgency_score,
        event.volatility_score,
        event.sentiment_label.value,
        event.volatility_risk.value,
        event.impact_level.value,
        event.trade_action,
        event.explanation,
        event.symbols_affected or None,
        event.asset_classes or None,
        json.dumps(event.model_dump(mode="json"), default=str),
    )
