-- db/migrations/009_level_discovery_audit.sql
--
-- Extends level_discovery_runs with scheduler trigger context and merge metadata.
-- runs_coalesced already exists in 008; application inserts are updated separately.

ALTER TABLE level_discovery_runs ADD COLUMN IF NOT EXISTS trigger_reason TEXT;
ALTER TABLE level_discovery_runs ADD COLUMN IF NOT EXISTS merge_mode TEXT;
ALTER TABLE level_discovery_runs ADD COLUMN IF NOT EXISTS regime_gap_pct DOUBLE PRECISION;
ALTER TABLE level_discovery_runs ADD COLUMN IF NOT EXISTS envelope_min DOUBLE PRECISION;
ALTER TABLE level_discovery_runs ADD COLUMN IF NOT EXISTS envelope_max DOUBLE PRECISION;

-- trigger_reason: range_escape | interval | regime_shift | manual | startup
-- merge_mode: drift | regime_shift (NULL on skip/error)
-- regime_gap_pct: % outside envelope when range_escape triggered
-- envelope_min/max: active price_levels book envelope before merge
