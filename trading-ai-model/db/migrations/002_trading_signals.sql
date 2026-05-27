-- 002_trading_signals.sql — pipeline observations + trade signal registry

CREATE TABLE IF NOT EXISTS pipeline_observations (
    id          BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    signal_rank INT,
    payload     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_time
    ON pipeline_observations (observed_at DESC);

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

CREATE TABLE IF NOT EXISTS trade_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(16) NOT NULL,
    timeframe       VARCHAR(8)  NOT NULL,
    signal_rank     INT,
    action          VARCHAR(32),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trade_signals_symbol
    ON trade_signals (symbol, created_at DESC);
