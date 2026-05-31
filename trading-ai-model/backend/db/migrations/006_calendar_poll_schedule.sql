-- 006_calendar_poll_schedule.sql — event-triggered news polling (extensible providers)

CREATE TABLE IF NOT EXISTS calendar_scheduled_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id      TEXT NOT NULL,
    external_key     TEXT NOT NULL,
    event_name       TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    event_at_utc     TIMESTAMPTZ NOT NULL,
    impact_level     TEXT NOT NULL,
    source_ids       TEXT[] NOT NULL,
    affected_symbols TEXT[],
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_id, external_key)
);

CREATE TABLE IF NOT EXISTS calendar_poll_triggers (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id         UUID NOT NULL REFERENCES calendar_scheduled_events(id) ON DELETE CASCADE,
    trigger_at_utc   TIMESTAMPTZ NOT NULL,
    offset_minutes   INT NOT NULL,
    source_ids       TEXT[] NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    fired_at         TIMESTAMPTZ,
    UNIQUE (event_id, offset_minutes)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_at
    ON calendar_scheduled_events (event_at_utc);

CREATE INDEX IF NOT EXISTS idx_calendar_triggers_pending
    ON calendar_poll_triggers (trigger_at_utc)
    WHERE status = 'pending';
