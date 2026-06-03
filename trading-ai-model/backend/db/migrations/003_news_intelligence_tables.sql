-- 003_news_intelligence_tables.sql
-- Market News Intelligence Agent — Database Schema
-- Compatible with TimescaleDB + Postgres 14+
-- Run after: 001_market_data.sql, 002_trading_signals.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── 1. news_events ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS news_events (
    id               TEXT NOT NULL,
    source           VARCHAR(64)   NOT NULL,
    headline         TEXT          NOT NULL,
    summary          TEXT,
    url              TEXT,
    published_at     TIMESTAMPTZ   NOT NULL,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    event_type       VARCHAR(64)   NOT NULL DEFAULT 'unknown',
    news_mode        VARCHAR(32)   NOT NULL DEFAULT 'informational',
    sentiment_score  NUMERIC(6,4)  NOT NULL DEFAULT 0,
    impact_score     NUMERIC(6,4)  NOT NULL DEFAULT 0,
    urgency_score    NUMERIC(6,4)  NOT NULL DEFAULT 0,
    volatility_score NUMERIC(6,4)  NOT NULL DEFAULT 0,
    sentiment_label  VARCHAR(16)   NOT NULL DEFAULT 'neutral',
    volatility_risk  VARCHAR(16)   NOT NULL DEFAULT 'low',
    impact_level     VARCHAR(16)   NOT NULL DEFAULT 'low',
    trade_action     VARCHAR(32)   NOT NULL DEFAULT 'none',
    explanation      TEXT,
    symbols_affected TEXT[],
    asset_classes    TEXT[],
    raw_payload      JSONB         NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, published_at)
);

-- Timescale hypertable requires partition column in PK (upgrade legacy id-only PK)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'news_events'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        WHERE rel.relname = 'news_events' AND c.contype = 'p'
          AND pg_get_constraintdef(c.oid) LIKE '%published_at%'
    ) THEN
        ALTER TABLE news_events DROP CONSTRAINT IF EXISTS news_events_pkey;
        ALTER TABLE news_events ADD PRIMARY KEY (id, published_at);
    END IF;
END $$;

-- Upgrade legacy news_events (TEXT id / JSONB symbols) when present
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'news_events' AND column_name = 'ingested_at'
    ) THEN
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS volatility_score NUMERIC(6,4) NOT NULL DEFAULT 0;
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS volatility_risk VARCHAR(16) NOT NULL DEFAULT 'low';
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS trade_action VARCHAR(32) NOT NULL DEFAULT 'none';
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS explanation TEXT;
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS asset_classes TEXT[];
        ALTER TABLE news_events ADD COLUMN IF NOT EXISTS raw_payload JSONB NOT NULL DEFAULT '{}';
    END IF;
END $$;

SELECT create_hypertable('news_events', 'published_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_news_events_published
    ON news_events (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_impact
    ON news_events (impact_score DESC, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_mode
    ON news_events (news_mode, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_type
    ON news_events (event_type, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_symbols
    ON news_events USING GIN (symbols_affected);


-- ── 2. economic_events ─────────────────────────────────────

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

CREATE INDEX IF NOT EXISTS idx_econ_events_scheduled
    ON economic_events (scheduled_at ASC);
CREATE INDEX IF NOT EXISTS idx_econ_events_type
    ON economic_events (event_type, scheduled_at ASC);
CREATE INDEX IF NOT EXISTS idx_econ_events_impact
    ON economic_events (impact_level, scheduled_at ASC);


-- ── 3. symbol_news_impact ──────────────────────────────────

CREATE TABLE IF NOT EXISTS symbol_news_impact (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    news_event_id    TEXT          NOT NULL,
    symbol           VARCHAR(16)   NOT NULL,
    impact_direction SMALLINT      NOT NULL DEFAULT 0,
    confidence       NUMERIC(6,4)  NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (news_event_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_sym_impact_symbol
    ON symbol_news_impact (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sym_impact_event
    ON symbol_news_impact (news_event_id);
CREATE INDEX IF NOT EXISTS idx_sym_impact_direction
    ON symbol_news_impact (symbol, impact_direction, created_at DESC);


-- ── 4. news_sentiment_scores ───────────────────────────────

CREATE TABLE IF NOT EXISTS news_sentiment_scores (
    id                  UUID NOT NULL DEFAULT gen_random_uuid(),
    symbol              VARCHAR(16)   NOT NULL,
    window_minutes      INTEGER       NOT NULL,
    computed_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    avg_sentiment       NUMERIC(6,4)  NOT NULL DEFAULT 0,
    avg_impact          NUMERIC(6,4)  NOT NULL DEFAULT 0,
    max_urgency         NUMERIC(6,4)  NOT NULL DEFAULT 0,
    max_volatility      NUMERIC(6,4)  NOT NULL DEFAULT 0,
    event_count         INTEGER       NOT NULL DEFAULT 0,
    high_impact_count   INTEGER       NOT NULL DEFAULT 0,
    dominant_sentiment  VARCHAR(16),
    dominant_event_type VARCHAR(64),
    PRIMARY KEY (id, computed_at)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'news_sentiment_scores'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        WHERE rel.relname = 'news_sentiment_scores' AND c.contype = 'p'
          AND pg_get_constraintdef(c.oid) LIKE '%computed_at%'
    ) THEN
        ALTER TABLE news_sentiment_scores DROP CONSTRAINT IF EXISTS news_sentiment_scores_pkey;
        ALTER TABLE news_sentiment_scores ADD PRIMARY KEY (id, computed_at);
    END IF;
END $$;

SELECT create_hypertable('news_sentiment_scores', 'computed_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_sent_scores_symbol
    ON news_sentiment_scores (symbol, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sent_scores_window
    ON news_sentiment_scores (symbol, window_minutes, computed_at DESC);


-- ── 5. news_risk_windows ───────────────────────────────────

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

CREATE INDEX IF NOT EXISTS idx_risk_windows_active
    ON news_risk_windows (starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_risk_windows_symbols
    ON news_risk_windows USING GIN (affected_symbols);


-- ── 6. news_feature_snapshots ──────────────────────────────

CREATE TABLE IF NOT EXISTS news_feature_snapshots (
    id                       UUID NOT NULL DEFAULT gen_random_uuid(),
    signal_id                UUID,
    symbol                   VARCHAR(16)   NOT NULL,
    timeframe                VARCHAR(8)    NOT NULL,
    snapshot_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    news_sentiment_score     NUMERIC(6,4)  NOT NULL DEFAULT 0,
    news_impact_score        NUMERIC(6,4)  NOT NULL DEFAULT 0,
    news_urgency_score       NUMERIC(6,4)  NOT NULL DEFAULT 0,
    volatility_risk_score    NUMERIC(6,4)  NOT NULL DEFAULT 0,
    minutes_since_last_news  NUMERIC(8,2)  NOT NULL DEFAULT 9999,
    minutes_until_next_event NUMERIC(8,2)  NOT NULL DEFAULT 9999,
    high_impact_news_active  BOOLEAN       NOT NULL DEFAULT FALSE,
    breaking_news_active     BOOLEAN       NOT NULL DEFAULT FALSE,
    affected_symbol_match    BOOLEAN       NOT NULL DEFAULT FALSE,
    news_conflict_score      NUMERIC(6,4)  NOT NULL DEFAULT 0,
    trading_blocked          BOOLEAN       NOT NULL DEFAULT FALSE,
    reduce_size_recommended  BOOLEAN       NOT NULL DEFAULT FALSE,
    manual_approval_required BOOLEAN       NOT NULL DEFAULT FALSE,
    news_risk_reason         TEXT,
    latest_headline          TEXT,
    latest_event_type        VARCHAR(64),
    latest_sentiment_label   VARCHAR(16),
    PRIMARY KEY (id, snapshot_at)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'news_feature_snapshots'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        WHERE rel.relname = 'news_feature_snapshots' AND c.contype = 'p'
          AND pg_get_constraintdef(c.oid) LIKE '%snapshot_at%'
    ) THEN
        ALTER TABLE news_feature_snapshots DROP CONSTRAINT IF EXISTS news_feature_snapshots_pkey;
        ALTER TABLE news_feature_snapshots ADD PRIMARY KEY (id, snapshot_at);
    END IF;
END $$;

SELECT create_hypertable('news_feature_snapshots', 'snapshot_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_news_snapshots_signal
    ON news_feature_snapshots (signal_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_snapshots_symbol
    ON news_feature_snapshots (symbol, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_snapshots_blocked
    ON news_feature_snapshots (trading_blocked, snapshot_at DESC);


-- ── Helper views ───────────────────────────────────────────

CREATE OR REPLACE VIEW active_news_risk_windows AS
SELECT *
FROM news_risk_windows
WHERE starts_at <= NOW()
  AND ends_at   >= NOW();

CREATE OR REPLACE VIEW recent_high_impact_news AS
SELECT DISTINCT ON (sym)
    unnest(symbols_affected) AS sym,
    id, headline, event_type, impact_score, urgency_score,
    sentiment_label, volatility_risk, published_at, explanation
FROM news_events
WHERE impact_score >= 0.65
  AND published_at >= NOW() - INTERVAL '2 hours'
  AND symbols_affected IS NOT NULL
ORDER BY sym, published_at DESC;

COMMENT ON TABLE news_events IS
    'Every ingested and classified news article or economic event.';
COMMENT ON TABLE news_risk_windows IS
    'Pre/post-event trading restriction windows for the Risk Agent.';
COMMENT ON TABLE news_feature_snapshots IS
    'Full news state at the moment of every trade signal.';

-- Continuous aggregate (TimescaleDB only — skip silently on plain Postgres)
DO $$
BEGIN
    CREATE MATERIALIZED VIEW IF NOT EXISTS news_hourly_sentiment
    WITH (timescaledb.continuous) AS
    SELECT
        time_bucket('1 hour', published_at) AS bucket,
        unnest(symbols_affected)            AS symbol,
        AVG(sentiment_score)                AS avg_sentiment,
        MAX(impact_score)                   AS max_impact,
        MAX(urgency_score)                  AS max_urgency,
        COUNT(*)                            AS event_count,
        COUNT(*) FILTER (WHERE impact_level IN ('high','critical')) AS high_impact_count,
        MODE() WITHIN GROUP (ORDER BY event_type) AS dominant_event_type
    FROM news_events
    WHERE symbols_affected IS NOT NULL
    GROUP BY bucket, unnest(symbols_affected)
    WITH NO DATA;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Skipping news_hourly_sentiment continuous aggregate: %', SQLERRM;
END $$;

DO $$
BEGIN
    PERFORM add_continuous_aggregate_policy('news_hourly_sentiment',
        start_offset => INTERVAL '3 hours',
        end_offset   => INTERVAL '1 minute',
        schedule_interval => INTERVAL '5 minutes',
        if_not_exists => TRUE);
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Skipping continuous aggregate policy: %', SQLERRM;
END $$;
