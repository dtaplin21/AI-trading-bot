"""
data/storage/timeseries_store.py

Real TimescaleDB read/write for OHLCV bars.

Was: write() → pass, read() → []
Now: actual database operations with upsert and range queries

Connects to:
  - ChartWatchRunner — saves every incoming bar here
  - train_reversal_models.py — reads bars for training
  - FeaturePipeline — reads recent bars for live inference
  - BacktestEngine — reads full history for simulation
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from config.settings import get_settings

logger = logging.getLogger(__name__)


def _get_connection():
    from data.storage.pg_connect import connect_psycopg2

    settings = get_settings()
    url = (settings.database_url or os.getenv("DATABASE_URL", "")).strip()
    if not url:
        raise ValueError("DATABASE_URL not set")
    return connect_psycopg2(url)


def _database_configured() -> bool:
    settings = get_settings()
    url = (settings.database_url or os.getenv("DATABASE_URL", "")).strip()
    return bool(url)


def _normalize_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        ts = pd.Timestamp(value)
        if ts is pd.NaT:
            raise ValueError(f"Invalid timestamp: {value!r}")
        dt = ts.to_pydatetime()
        if not isinstance(dt, datetime):
            raise ValueError(f"Invalid timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class TimeseriesStore:
    """
    Read/write OHLCV bars to TimescaleDB ohlcv_candles table.
    """

    def __init__(self) -> None:
        self._available = _database_configured()

    @property
    def available(self) -> bool:
        return self._available

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(
        self,
        symbol: str,
        timeframe: str,
        bars: list[dict],
    ) -> int:
        """
        Upsert a list of OHLCV bars into the database.

        Args:
            symbol:    e.g. "EURUSD"
            timeframe: e.g. "1m", "5m"
            bars:      list of dicts with keys:
                       time, open, high, low, close, volume

        Returns:
            number of rows upserted
        """
        if not bars:
            return 0
        if not self._available:
            logger.debug("%s/%s: skip write — DATABASE_URL not set", symbol, timeframe)
            return 0

        sym = symbol.upper()
        conn = _get_connection()
        cur = conn.cursor()
        sql = """
            INSERT INTO ohlcv_candles
                (time, symbol, timeframe, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume
        """
        rows = [
            (
                _normalize_time(b["time"]),
                sym,
                timeframe,
                float(b["open"]),
                float(b["high"]),
                float(b["low"]),
                float(b["close"]),
                float(b.get("volume", 0)),
            )
            for b in bars
        ]

        try:
            cur.executemany(sql, rows)
            conn.commit()
            count = len(rows)
            logger.debug("%s/%s: upserted %d bars", sym, timeframe, count)
            return count
        except Exception as e:
            conn.rollback()
            logger.error("write error %s/%s: %s", sym, timeframe, e)
            raise
        finally:
            cur.close()
            conn.close()

    def write_bar(
        self,
        symbol: str,
        timeframe: str,
        time,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
    ) -> int:
        """Write a single bar. Convenience wrapper around write()."""
        return self.write(
            symbol,
            timeframe,
            [
                {
                    "time": time,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            ],
        )

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Read OHLCV bars from the database.

        Returns DataFrame indexed by time with columns:
        open, high, low, close, volume
        """
        if not self._available:
            return pd.DataFrame()

        sym = symbol.upper()
        conditions = ["symbol = %s", "timeframe = %s"]
        params: list[Any] = [sym, timeframe]

        if start:
            conditions.append("time >= %s")
            params.append(start)
        if end:
            conditions.append("time <= %s")
            params.append(end)

        where = " AND ".join(conditions)
        order = "ORDER BY time ASC"
        lim = f"LIMIT {int(limit)}" if limit else ""

        sql = f"""
            SELECT time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE {where}
            {order}
            {lim}
        """

        try:
            conn = _get_connection()
            df = pd.read_sql(sql, conn, params=params)
            conn.close()

            if df.empty:
                return df

            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df.set_index("time")

        except Exception as e:
            logger.error("read error %s/%s: %s", sym, timeframe, e)
            return pd.DataFrame()

    def read_latest(
        self,
        symbol: str,
        timeframe: str,
        n: int = 500,
    ) -> pd.DataFrame:
        """Read the most recent N bars for a symbol."""
        if not self._available:
            return pd.DataFrame()

        sym = symbol.upper()
        try:
            conn = _get_connection()
            sql = """
                SELECT time, open, high, low, close, volume
                FROM ohlcv_candles
                WHERE symbol = %s AND timeframe = %s
                ORDER BY time DESC
                LIMIT %s
            """
            df = pd.read_sql(sql, conn, params=(sym, timeframe, n))
            conn.close()

            if df.empty:
                return df

            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df.set_index("time").sort_index()

        except Exception as e:
            logger.error("read_latest error %s/%s: %s", sym, timeframe, e)
            return pd.DataFrame()

    def count(self, symbol: str, timeframe: str) -> int:
        """Count bars in the database for a symbol/timeframe."""
        if not self._available:
            return 0

        sym = symbol.upper()
        try:
            conn = _get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM ohlcv_candles WHERE symbol=%s AND timeframe=%s",
                (sym, timeframe),
            )
            count = cur.fetchone()[0]
            cur.close()
            conn.close()
            return int(count)
        except Exception as e:
            logger.error("count error: %s", e)
            return 0

    def latest_timestamp(
        self, symbol: str, timeframe: str
    ) -> Optional[datetime]:
        """Get the timestamp of the most recent bar."""
        if not self._available:
            return None

        sym = symbol.upper()
        try:
            conn = _get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT MAX(time) FROM ohlcv_candles WHERE symbol=%s AND timeframe=%s",
                (sym, timeframe),
            )
            result = cur.fetchone()[0]
            cur.close()
            conn.close()
            if result is None:
                return None
            if isinstance(result, datetime) and result.tzinfo is None:
                return result.replace(tzinfo=timezone.utc)
            return result
        except Exception:
            return None


# Module-level singleton
_store = TimeseriesStore()


def get_timeseries_store() -> TimeseriesStore:
    return _store


def write_bars(symbol: str, timeframe: str, bars: list[dict]) -> int:
    return _store.write(symbol, timeframe, bars)


def read_bars(
    symbol: str,
    timeframe: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    return _store.read(symbol, timeframe, start, end, limit)


def read_latest_bars(symbol: str, timeframe: str, n: int = 500) -> pd.DataFrame:
    return _store.read_latest(symbol, timeframe, n)
