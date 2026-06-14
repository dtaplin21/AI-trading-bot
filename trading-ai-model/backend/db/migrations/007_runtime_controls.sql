-- 007_runtime_controls.sql — cross-process runtime toggles (kill switch, etc.)

CREATE TABLE IF NOT EXISTS runtime_controls (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO runtime_controls (key, value)
VALUES ('kill_switch', '{"enabled": false}'::jsonb)
ON CONFLICT (key) DO NOTHING;
