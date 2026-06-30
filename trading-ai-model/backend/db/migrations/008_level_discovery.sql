-- db/migrations/008_level_discovery.sql
--
-- Adds archive table for old/inactive price levels (preserve, never delete)
-- and an audit table for rolling discovery run history.

-- Archive table: same shape as price_levels, holds levels that have aged out
CREATE TABLE IF NOT EXISTS price_levels_archive (
    LIKE price_levels INCLUDING ALL
);
ALTER TABLE price_levels_archive ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE price_levels_archive ADD COLUMN IF NOT EXISTS archive_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_price_levels_archive_symbol_price
    ON price_levels_archive (symbol, level_price);

-- Track discovery provenance on the live table without disturbing existing columns
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS discovery_source TEXT DEFAULT 'seed';
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS last_discovery_at TIMESTAMPTZ;

-- Audit trail for every rolling discovery run (success, skip, or error)
CREATE TABLE IF NOT EXISTS level_discovery_runs (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              TEXT NOT NULL,
    window_days         INT NOT NULL,
    bars_loaded         INT NOT NULL,
    bars_expected       INT NOT NULL,
    coverage_pct        NUMERIC(5,2) NOT NULL,
    levels_found        INT NOT NULL DEFAULT 0,
    levels_merged       INT NOT NULL DEFAULT 0,
    levels_archived     INT NOT NULL DEFAULT 0,
    levels_reactivated  INT NOT NULL DEFAULT 0,
    watchlist_active    INT NOT NULL DEFAULT 0,
    last_close          DOUBLE PRECISION,
    skipped_reason      TEXT,
    runs_coalesced      INT NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_symbol
    ON level_discovery_runs (symbol, started_at DESC);
