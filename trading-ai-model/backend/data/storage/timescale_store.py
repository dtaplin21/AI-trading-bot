"""TimescaleDB / Postgres interface for OHLCV and observations."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator, Optional

import pandas as pd

from config.settings import get_settings
from data.storage.migrate import split_sql
from data.storage.pg_connect import connect_psycopg2
from data.storage.news_repository import (
    NEWS_EVENTS_V2_COLUMNS,
    NEWS_TABLES_DDL,
    economic_event_row,
    news_event_insert_row,
    news_features_row,
    risk_window_row,
    row_to_economic_event,
    row_to_news_event,
    row_to_risk_window,
)

logger = logging.getLogger(__name__)

OHLCV_DDL = """
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
"""

OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_observations (
    id          BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    signal_rank INT,
    payload     JSONB NOT NULL
);
"""

MODEL_REGISTRY_DDL = """
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
"""

NEWS_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS news_events (
    id               TEXT NOT NULL,
    source           TEXT NOT NULL,
    headline         TEXT NOT NULL,
    summary          TEXT,
    url              TEXT,
    published_at     TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type       TEXT NOT NULL DEFAULT 'unknown',
    news_mode        TEXT NOT NULL DEFAULT 'informational',
    sentiment_score  DOUBLE PRECISION NOT NULL DEFAULT 0,
    impact_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
    urgency_score    DOUBLE PRECISION NOT NULL DEFAULT 0,
    volatility_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    sentiment_label  TEXT NOT NULL DEFAULT 'neutral',
    volatility_risk  TEXT NOT NULL DEFAULT 'low',
    impact_level     TEXT NOT NULL DEFAULT 'low',
    trade_action     TEXT NOT NULL DEFAULT 'none',
    explanation      TEXT,
    symbols_affected TEXT[],
    asset_classes    TEXT[],
    raw_payload      JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, published_at)
);
"""

SYMBOL_IMPACTS_DDL = """
CREATE TABLE IF NOT EXISTS symbol_news_impacts (
    news_event_id   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    impact_direction INT,
    confidence      DOUBLE PRECISION,
    PRIMARY KEY (news_event_id, symbol)
);
"""

CONFLUENCE_OUTCOMES_DDL = """
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
"""

PLANNER_AUDITS_DDL = """
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
"""

CALENDAR_SCHEDULE_DDL = """
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
"""


class TimescaleStore:
    """Postgres/TimescaleDB store with graceful fallback when DB unavailable."""

    def __init__(self, database_url: Optional[str] = None):
        settings = get_settings()
        self.database_url = database_url or settings.database_url
        self._available = False
        self._use_symbol_impact_v2 = False
        if self.database_url:
            self._init_connection()

    def _init_connection(self) -> None:
        try:
            import psycopg2  # noqa: PLC0415

            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(OHLCV_DDL)
                    cur.execute(OBSERVATIONS_DDL)
                    cur.execute(MODEL_REGISTRY_DDL)
                    cur.execute(NEWS_EVENTS_DDL)
                    cur.execute(SYMBOL_IMPACTS_DDL)
                    cur.execute(CONFLUENCE_OUTCOMES_DDL)
                    cur.execute(PLANNER_AUDITS_DDL)
                    cur.execute(CALENDAR_SCHEDULE_DDL)
                    for stmt in split_sql(NEWS_TABLES_DDL):
                        cur.execute(stmt)
                    try:
                        cur.execute(NEWS_EVENTS_V2_COLUMNS)
                    except Exception:
                        pass
                    self._use_symbol_impact_v2 = self._table_exists(cur, "symbol_news_impact")
                    try:
                        cur.execute(
                            "SELECT create_hypertable('ohlcv_candles', 'time', if_not_exists => TRUE);"
                        )
                        cur.execute(
                            "SELECT create_hypertable('news_events', 'published_at', if_not_exists => TRUE);"
                        )
                    except Exception:
                        logger.debug("TimescaleDB extension not present — using plain Postgres tables")
                conn.commit()
            self._available = True
        except Exception as exc:
            logger.warning("Database unavailable: %s", exc)
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @contextmanager
    def _connect(self) -> Generator[Any, None, None]:
        conn = connect_psycopg2(self.database_url)
        try:
            yield conn
        finally:
            conn.close()

    def upsert_ohlcv(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        if not self._available or df.empty:
            return 0

        rows = []
        for ts, row in df.iterrows():
            t = pd.Timestamp(ts).to_pydatetime()
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            rows.append(
                (
                    t,
                    symbol.upper(),
                    timeframe,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume", 0)),
                )
            )

        sql = """
            INSERT INTO ohlcv_candles (time, symbol, timeframe, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def load_ohlcv(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: int = 500,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        clauses = ["symbol = %s", "timeframe = %s"]
        params: list[Any] = [symbol.upper(), timeframe]
        if start:
            clauses.append("time >= %s")
            params.append(start)
        if end:
            clauses.append("time <= %s")
            params.append(end)
        params.append(limit)

        sql = f"""
            SELECT time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE {' AND '.join(clauses)}
            ORDER BY time DESC
            LIMIT %s
        """
        with self._connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = df.sort_values("time").set_index("time")
        return df[["open", "high", "low", "close", "volume"]]

    def load_ohlcv_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 500_000,
    ) -> pd.DataFrame:
        """Load OHLCV in [start, end] ascending — for chart watcher replay."""
        if not self._available:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        start_t = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end_t = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

        sql = """
            SELECT time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = %s AND timeframe = %s
              AND time >= %s AND time <= %s
            ORDER BY time ASC
            LIMIT %s
        """
        params = (symbol.upper(), timeframe, start_t, end_t, limit)
        with self._connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return df.set_index("time")[["open", "high", "low", "close", "volume"]]

    def latest_bar_time(self, symbol: str, timeframe: str = "5m") -> Optional[datetime]:
        if not self._available:
            return None
        sql = """
            SELECT time FROM ohlcv_candles
            WHERE symbol = %s AND timeframe = %s
            ORDER BY time DESC LIMIT 1
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol.upper(), timeframe))
                row = cur.fetchone()
        if not row:
            return None
        t = row[0]
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def count_bars(self, symbol: str, timeframe: str = "5m") -> int:
        if not self._available:
            return 0
        sql = """
            SELECT COUNT(*) FROM ohlcv_candles
            WHERE symbol = %s AND timeframe = %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol.upper(), timeframe))
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def insert_observation(self, symbol: str, timeframe: str, signal_rank: int, payload: dict) -> None:
        if not self._available:
            return
        sql = """
            INSERT INTO pipeline_observations (symbol, timeframe, signal_rank, payload)
            VALUES (%s, %s, %s, %s::jsonb)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol, timeframe, signal_rank, json.dumps(payload, default=str)))
            conn.commit()

    def load_observations(self, limit: int = 10000) -> list[dict]:
        if not self._available:
            return []
        sql = """
            SELECT payload FROM pipeline_observations
            ORDER BY observed_at DESC LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()
        return [r[0] for r in rows]

    def insert_news_events(self, events: list) -> int:
        if not self._available or not events:
            return 0
        sql = """
            INSERT INTO news_events (
                id, source, headline, summary, url, published_at, created_at,
                event_type, news_mode, sentiment_score, impact_score, urgency_score,
                volatility_score, sentiment_label, volatility_risk, impact_level,
                trade_action, explanation, symbols_affected, asset_classes, raw_payload
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            ON CONFLICT (id, published_at) DO NOTHING
        """
        rows = [news_event_insert_row(e) for e in events]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def insert_symbol_impacts(self, impacts: list) -> int:
        if not self._available or not impacts:
            return 0
        if self._use_symbol_impact_v2:
            sql = """
                INSERT INTO symbol_news_impact (news_event_id, symbol, impact_direction, confidence)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (news_event_id, symbol) DO UPDATE SET
                    impact_direction = EXCLUDED.impact_direction,
                    confidence = EXCLUDED.confidence
            """
        else:
            sql = """
                INSERT INTO symbol_news_impacts (news_event_id, symbol, impact_direction, confidence)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (news_event_id, symbol) DO UPDATE SET
                    impact_direction = EXCLUDED.impact_direction,
                    confidence = EXCLUDED.confidence
            """
        rows = [(i.news_event_id, i.symbol, i.impact_direction, i.confidence) for i in impacts]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def fetch_recent_news_events(
        self,
        hours: int = 6,
        symbol: Optional[str] = None,
        limit: int = 500,
    ) -> list:
        if not self._available:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        sql = """
            SELECT id, source, headline, summary, url, published_at, created_at,
                   event_type, news_mode, sentiment_score, impact_score, urgency_score,
                   volatility_score, sentiment_label, volatility_risk, impact_level,
                   trade_action, explanation, symbols_affected, asset_classes
            FROM news_events
            WHERE published_at >= %s
        """
        params: list[Any] = [cutoff]
        if symbol:
            sql += " AND (%s = ANY(symbols_affected) OR news_mode = 'risk_event')"
            params.append(symbol.upper())
        sql += " ORDER BY published_at DESC LIMIT %s"
        params.append(limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return [row_to_news_event(r) for r in rows]

    def insert_economic_event(self, event) -> None:
        if not self._available:
            return
        sql = """
            INSERT INTO economic_events (
                id, event_name, event_type, scheduled_at, country, impact_level, source,
                forecast_value, actual_value, previous_value, surprise_pct, affected_symbols
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, economic_event_row(event))
            conn.commit()

    def insert_risk_windows(self, windows: list) -> int:
        if not self._available or not windows:
            return 0
        sql = """
            INSERT INTO news_risk_windows (
                id, event_name, event_type, starts_at, ends_at, affected_symbols,
                risk_level, trading_allowed, reduce_size, require_manual, reason
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
        """
        rows = [risk_window_row(w) for w in windows]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def insert_news_feature_snapshot(
        self,
        features,
        symbol: str,
        timeframe: str,
        signal_id: Optional[str] = None,
    ) -> None:
        if not self._available:
            return
        sql = """
            INSERT INTO news_feature_snapshots (
                signal_id, symbol, timeframe,
                news_sentiment_score, news_impact_score, news_urgency_score, volatility_risk_score,
                minutes_since_last_news, minutes_until_next_event,
                high_impact_news_active, breaking_news_active, affected_symbol_match,
                news_conflict_score, trading_blocked, reduce_size_recommended,
                manual_approval_required, news_risk_reason,
                latest_headline, latest_event_type, latest_sentiment_label
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, news_features_row(features, symbol, timeframe, signal_id))
            conn.commit()

    def fetch_active_risk_windows(
        self,
        symbol: str,
        at: Optional[datetime] = None,
    ) -> list:
        if not self._available:
            return []
        now = at or datetime.now(timezone.utc)
        sql = """
            SELECT id, event_name, event_type, starts_at, ends_at, affected_symbols,
                   risk_level, trading_allowed, reduce_size, require_manual, reason, created_at
            FROM news_risk_windows
            WHERE starts_at <= %s AND ends_at >= %s
              AND (affected_symbols IS NULL
                   OR cardinality(affected_symbols) = 0
                   OR %s = ANY(affected_symbols))
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (now, now, symbol.upper()))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return [row_to_risk_window(r) for r in rows]

    def fetch_upcoming_economic_events(self, hours_ahead: int = 48) -> list:
        if not self._available:
            return []
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        sql = """
            SELECT id, event_name, event_type, scheduled_at, country, impact_level, source,
                   forecast_value, actual_value, previous_value, surprise_pct,
                   affected_symbols, created_at
            FROM economic_events
            WHERE scheduled_at > %s AND scheduled_at <= %s
            ORDER BY scheduled_at ASC
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (now, cutoff))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return [row_to_economic_event(r) for r in rows]

    def insert_confluence_outcome(self, row: dict, confluence: dict | None = None) -> None:
        """Upsert one labeled training row for the learning loop."""
        if not self._available:
            return
        sql = """
            INSERT INTO confluence_outcomes (
                snapshot_id, symbol, timeframe, regime, signal_rank,
                predicted_p_success, predicted_ev, outcome_label,
                actual_pnl, actual_r_multiple, hit_target, hit_stop,
                scored_at, closed_at, training_row, confluence
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb
            )
            ON CONFLICT (snapshot_id) DO UPDATE SET
                outcome_label = EXCLUDED.outcome_label,
                actual_pnl = EXCLUDED.actual_pnl,
                actual_r_multiple = EXCLUDED.actual_r_multiple,
                hit_target = EXCLUDED.hit_target,
                hit_stop = EXCLUDED.hit_stop,
                closed_at = EXCLUDED.closed_at,
                training_row = EXCLUDED.training_row,
                confluence = COALESCE(EXCLUDED.confluence, confluence_outcomes.confluence)
        """
        scored_raw = row.get("_timestamp")
        scored_at = None
        if scored_raw:
            try:
                scored_at = datetime.fromisoformat(str(scored_raw).replace("Z", "+00:00"))
            except ValueError:
                scored_at = None
        closed_at = datetime.now(tz=timezone.utc)
        payload = json.dumps(row, default=str)
        conf_payload = json.dumps(confluence, default=str) if confluence else None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        row.get("snapshot_id"),
                        row.get("_symbol"),
                        row.get("_timeframe"),
                        row.get("_regime"),
                        row.get("signal_rank"),
                        row.get("predicted_p_success"),
                        row.get("predicted_ev"),
                        row.get("label"),
                        row.get("actual_pnl"),
                        row.get("actual_r"),
                        bool(row.get("hit_target")),
                        bool(row.get("hit_stop")),
                        scored_at,
                        closed_at,
                        payload,
                        conf_payload,
                    ),
                )
            conn.commit()

    def load_confluence_outcomes(self, limit: int = 50000) -> list[dict]:
        """Load labeled training rows newest-first."""
        if not self._available:
            return []
        sql = """
            SELECT training_row FROM confluence_outcomes
            ORDER BY closed_at DESC
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()
        return [r[0] for r in rows if r and r[0]]

    def upsert_calendar_event(self, draft) -> str:
        if not self._available:
            return ""
        sql = """
            INSERT INTO calendar_scheduled_events (
                provider_id, external_key, event_name, event_type,
                event_at_utc, impact_level, source_ids, affected_symbols
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (provider_id, external_key) DO UPDATE SET
                event_name = EXCLUDED.event_name,
                event_type = EXCLUDED.event_type,
                event_at_utc = EXCLUDED.event_at_utc,
                impact_level = EXCLUDED.impact_level,
                source_ids = EXCLUDED.source_ids,
                affected_symbols = EXCLUDED.affected_symbols,
                synced_at = NOW()
            RETURNING id
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        draft.provider_id,
                        draft.external_key,
                        draft.event_name,
                        draft.event_type.value if hasattr(draft.event_type, "value") else draft.event_type,
                        draft.event_at_utc,
                        draft.impact_level.value if hasattr(draft.impact_level, "value") else draft.impact_level,
                        draft.source_ids,
                        draft.affected_symbols or None,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return str(row[0]) if row else ""

    def insert_calendar_trigger(
        self,
        event_id: str,
        trigger_at_utc: datetime,
        offset_minutes: int,
        source_ids: list[str],
    ) -> bool:
        if not self._available:
            return False
        sql = """
            INSERT INTO calendar_poll_triggers (
                event_id, trigger_at_utc, offset_minutes, source_ids, status
            ) VALUES (%s,%s,%s,%s,'pending')
            ON CONFLICT (event_id, offset_minutes) DO NOTHING
            RETURNING id
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (event_id, trigger_at_utc, offset_minutes, source_ids))
                created = cur.fetchone() is not None
            conn.commit()
        return created

    def fetch_due_calendar_triggers(self, now: datetime, limit: int) -> list:
        from agents.news.calendar.schemas import CalendarPollTrigger

        if not self._available:
            return []
        sql = """
            SELECT id, event_id, trigger_at_utc, offset_minutes, source_ids, status, fired_at
            FROM calendar_poll_triggers
            WHERE status = 'pending' AND trigger_at_utc <= %s
            ORDER BY trigger_at_utc ASC
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (now, limit))
                rows = cur.fetchall()
        return [self._calendar_trigger_row(r) for r in rows]

    def fetch_catchup_calendar_triggers(self, now: datetime, since: datetime) -> list:
        if not self._available:
            return []
        sql = """
            SELECT id, event_id, trigger_at_utc, offset_minutes, source_ids, status, fired_at
            FROM calendar_poll_triggers
            WHERE status = 'pending' AND trigger_at_utc <= %s AND trigger_at_utc >= %s
            ORDER BY trigger_at_utc ASC
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (now, since))
                rows = cur.fetchall()
        return [self._calendar_trigger_row(r) for r in rows]

    def delete_calendar_trigger(self, trigger_id: str) -> None:
        if not self._available:
            return
        sql = "DELETE FROM calendar_poll_triggers WHERE id = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (trigger_id,))
            conn.commit()

    def cleanup_calendar_events(self) -> int:
        if not self._available:
            return 0
        sql = """
            DELETE FROM calendar_scheduled_events e
            WHERE e.event_at_utc < NOW() - INTERVAL '1 hour'
              AND NOT EXISTS (
                SELECT 1 FROM calendar_poll_triggers t
                WHERE t.event_id = e.id AND t.status = 'pending'
              )
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                n = cur.rowcount
            conn.commit()
        return n

    def count_calendar_triggers_fired_since(self, since: datetime) -> int:
        return 0

    def next_calendar_trigger_at(self) -> datetime | None:
        if not self._available:
            return None
        sql = """
            SELECT MIN(trigger_at_utc) FROM calendar_poll_triggers
            WHERE status = 'pending' AND trigger_at_utc > NOW()
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        return row[0] if row and row[0] else None

    @staticmethod
    def _calendar_trigger_row(row) -> "CalendarPollTrigger":
        from agents.news.calendar.schemas import CalendarPollTrigger

        return CalendarPollTrigger(
            id=str(row[0]),
            event_id=str(row[1]),
            trigger_at_utc=row[2],
            offset_minutes=int(row[3]),
            source_ids=list(row[4] or []),
            status=row[5],
            fired_at=row[6],
        )

    def insert_planner_audit(self, record: dict) -> None:
        if not self._available:
            return
        sql = """
            INSERT INTO planner_decision_audits (
                snapshot_id, symbol, timeframe, planner, route_reason,
                confluence_score, conflict_score, news_aligned,
                p_success, ev_dollars, signal_rank,
                chosen_action, plan_ev, plan_confidence,
                rollouts, exploration_c, root_value,
                best_path, path_state, alternative_paths, search_stats, full_audit
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb
            )
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        record.get("snapshot_id"),
                        record.get("symbol"),
                        record.get("timeframe"),
                        record.get("planner"),
                        record.get("route_reason"),
                        record.get("confluence_score"),
                        record.get("conflict_score"),
                        record.get("news_aligned"),
                        record.get("p_success"),
                        record.get("ev_dollars"),
                        record.get("signal_rank"),
                        record.get("chosen_action"),
                        record.get("plan_ev"),
                        record.get("plan_confidence"),
                        record.get("rollouts"),
                        record.get("exploration_c"),
                        record.get("root_value"),
                        json.dumps(record.get("best_path"), default=str)
                        if record.get("best_path") is not None
                        else None,
                        json.dumps(record.get("path_state"), default=str)
                        if record.get("path_state") is not None
                        else None,
                        json.dumps(record.get("alternative_paths"), default=str)
                        if record.get("alternative_paths") is not None
                        else None,
                        json.dumps(record.get("search_stats"), default=str)
                        if record.get("search_stats") is not None
                        else None,
                        json.dumps(record.get("full_audit"), default=str),
                    ),
                )
            conn.commit()

    def _table_exists(self, cur, table: str) -> bool:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return cur.fetchone() is not None
