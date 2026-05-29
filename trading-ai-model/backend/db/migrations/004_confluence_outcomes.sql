-- 004_confluence_outcomes.sql — labeled ML training rows (WorldStateStore)

CREATE TABLE IF NOT EXISTS confluence_outcomes (
    snapshot_id         TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    regime              TEXT,
    signal_rank         INT,
    predicted_p_success DOUBLE PRECISION,
    predicted_ev        DOUBLE PRECISION,
    outcome_label       SMALLINT NOT NULL,
    actual_pnl          DOUBLE PRECISION,
    actual_r_multiple   DOUBLE PRECISION,
    hit_target          BOOLEAN NOT NULL DEFAULT FALSE,
    hit_stop            BOOLEAN NOT NULL DEFAULT FALSE,
    scored_at           TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    training_row        JSONB NOT NULL,
    confluence          JSONB
);

CREATE INDEX IF NOT EXISTS idx_confluence_outcomes_closed
    ON confluence_outcomes (closed_at DESC);

CREATE INDEX IF NOT EXISTS idx_confluence_outcomes_symbol
    ON confluence_outcomes (symbol, timeframe, closed_at DESC);
