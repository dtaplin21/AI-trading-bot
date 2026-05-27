"""TimescaleDB / Postgres interface for OHLCV and observations."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator, Optional

import pandas as pd

from config.settings import get_settings
from data.storage.news_repository import (
    NEWS_EVENTS_V2_COLUMNS,
    NEWS_TABLES_DDL,
    economic_event_row,
    news_event_insert_row,
    news_features_row,
    risk_window_row,
    row_to_economic_event,
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
    id               TEXT PRIMARY KEY,
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
    raw_payload      JSONB NOT NULL DEFAULT '{}'
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
                    cur.execute(NEWS_TABLES_DDL)
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
        import psycopg2  # noqa: PLC0415

        conn = psycopg2.connect(self.database_url)
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
            ON CONFLICT (id) DO NOTHING
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

    def _table_exists(self, cur, table: str) -> bool:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return cur.fetchone() is not None
