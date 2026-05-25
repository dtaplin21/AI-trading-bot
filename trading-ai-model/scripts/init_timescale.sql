-- TimescaleDB init (run once against your Postgres instance)
-- psql $DATABASE_URL -f scripts/init_timescale.sql

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS ohlcv_candles (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL DEFAULT '5m',
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (time, symbol, timeframe)
);

SELECT create_hypertable('ohlcv_candles', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time ON ohlcv_candles (symbol, timeframe, time DESC);

CREATE TABLE IF NOT EXISTS pipeline_observations (
    id          BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    signal_rank INT,
    payload     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_time ON pipeline_observations (observed_at DESC);

CREATE TABLE IF NOT EXISTS model_registry (
    id              TEXT PRIMARY KEY,
    version         TEXT NOT NULL,
    stage           TEXT NOT NULL DEFAULT 'candidate',
    metrics         JSONB,
    artifact_path   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ,
    approved_by     TEXT,
    promoted_at     TIMESTAMPTZ
);
