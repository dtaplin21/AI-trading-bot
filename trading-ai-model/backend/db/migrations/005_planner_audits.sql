-- 005_planner_audits.sql — compact MCTS / beam search audit (not full trees)

CREATE TABLE IF NOT EXISTS planner_decision_audits (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id         TEXT,
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    planner             TEXT NOT NULL,
    route_reason        TEXT,
    confluence_score    DOUBLE PRECISION,
    conflict_score      DOUBLE PRECISION,
    news_aligned        BOOLEAN,
    p_success           DOUBLE PRECISION,
    ev_dollars          DOUBLE PRECISION,
    signal_rank         INT,
    chosen_action       TEXT,
    plan_ev             DOUBLE PRECISION,
    plan_confidence     DOUBLE PRECISION,
    rollouts            INT,
    exploration_c       DOUBLE PRECISION,
    root_value          DOUBLE PRECISION,
    best_path           JSONB,
    path_state          JSONB,
    alternative_paths   JSONB,
    search_stats        JSONB,
    full_audit          JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_planner_audits_decided
    ON planner_decision_audits (decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_planner_audits_planner
    ON planner_decision_audits (planner, decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_planner_audits_snapshot
    ON planner_decision_audits (snapshot_id)
    WHERE snapshot_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_planner_audits_symbol
    ON planner_decision_audits (symbol, timeframe, decided_at DESC);
