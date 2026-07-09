"""
Classify level_touches outcomes (pending → hold/break) from forward OHLCV bars.

Shared by live process_bar resolution and scripts/backfill_touch_outcomes.py.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder

logger = logging.getLogger("touch_outcome_classifier")

OUTCOME_WINDOW = int(os.getenv("LEVEL_INTEL_OUTCOME_WINDOW", "20"))
REVERSAL_PCT = float(os.getenv("LEVEL_INTEL_REVERSAL_PCT", "0.15"))
BACKFILL_BATCH_SIZE = int(os.getenv("TOUCH_OUTCOME_BACKFILL_BATCH", "500"))
LIVE_DRAIN_BATCH_SIZE = int(os.getenv("TOUCH_OUTCOME_LIVE_DRAIN_BATCH", "25"))


@dataclass(frozen=True)
class TouchOutcome:
    outcome: str
    price_move_after: float
    bars_to_outcome: int


def compute_outcome(
    price_at_touch: float,
    approach: str,
    future_high: float,
    future_low: float,
    *,
    reversal_pct: float | None = None,
) -> TouchOutcome:
    """Mirror live _resolve_pending hold/break rules."""
    rev = (reversal_pct if reversal_pct is not None else REVERSAL_PCT) / 100.0
    up_move = (future_high - price_at_touch) / (price_at_touch + 1e-10)
    down_move = (price_at_touch - future_low) / (price_at_touch + 1e-10)

    if approach == "from_above":
        if up_move >= rev:
            return TouchOutcome("hold", round(up_move * 100, 4), OUTCOME_WINDOW)
        return TouchOutcome("break", round(-down_move * 100, 4), OUTCOME_WINDOW)

    if down_move >= rev:
        return TouchOutcome("hold", round(-down_move * 100, 4), OUTCOME_WINDOW)
    return TouchOutcome("break", round(up_move * 100, 4), OUTCOME_WINDOW)


def bar_index_for_timestamp(df, touched_at: datetime) -> int | None:
    """Map touched_at to bar index in a time-indexed OHLCV dataframe."""
    import pandas as pd

    if df is None or df.empty:
        return None
    index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    bar_time = _parse_ts(touched_at)
    idx = int(index.searchsorted(bar_time))
    if idx >= len(index):
        idx = len(index) - 1
    if idx < 0:
        return None
    if bar_time not in index and idx > 0:
        prev_delta = abs((index[idx - 1] - bar_time).total_seconds())
        next_delta = abs((index[idx] - bar_time).total_seconds())
        if prev_delta <= next_delta:
            idx -= 1
    return idx


def _parse_ts(val: Any) -> datetime:
    if isinstance(val, datetime):
        ts = val
    else:
        ts = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def classify_from_forward_bars(
    price_at_touch: float,
    approach: str,
    highs: list[float] | np.ndarray,
    lows: list[float] | np.ndarray,
) -> TouchOutcome | None:
    if len(highs) < 1 or len(lows) < 1:
        return None
    future_high = float(np.max(highs))
    future_low = float(np.min(lows))
    result = compute_outcome(price_at_touch, approach, future_high, future_low)
    return TouchOutcome(
        result.outcome,
        result.price_move_after,
        min(len(highs), OUTCOME_WINDOW),
    )


def load_forward_bars_conn(
    conn,
    symbol: str,
    touched_at: datetime,
    *,
    window_bars: int | None = None,
) -> tuple[list[float], list[float]]:
    """Load up to N 1m bars strictly after touched_at."""
    window = window_bars or OUTCOME_WINDOW
    ts = _parse_ts(touched_at)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT high, low
        FROM ohlcv_candles
        WHERE symbol = %s
          AND timeframe = '1m'
          AND time > %s
          AND close > 0
        ORDER BY time ASC
        LIMIT %s
        """,
        (symbol.upper(), ts, window),
    )
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return [], []
    highs = [float(r[0]) for r in rows]
    lows = [float(r[1]) for r in rows]
    return highs, lows


def classify_touch_row(
    conn,
    *,
    symbol: str,
    price_at_touch: float,
    approach: str,
    touched_at: datetime,
) -> TouchOutcome | None:
    highs, lows = load_forward_bars_conn(conn, symbol, touched_at)
    return classify_from_forward_bars(price_at_touch, approach, highs, lows)


def reaggregate_price_levels(conn, symbol: str) -> None:
    """Rebuild price_levels touch/hold/break counts from classified level_touches."""
    sym = symbol.upper()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE price_levels pl
        SET
            touch_count      = sub.total,
            hold_count       = sub.holds,
            break_count      = sub.breaks,
            support_count    = sub.support,
            resistance_count = sub.resistance,
            hold_rate        = sub.holds::FLOAT / NULLIF(sub.total, 0),
            last_touched     = sub.last_ts,
            role = CASE
                WHEN sub.support::FLOAT / NULLIF(sub.support + sub.resistance, 0) >= 0.65
                    THEN 'SUPPORT'
                WHEN sub.resistance::FLOAT / NULLIF(sub.support + sub.resistance, 0) >= 0.65
                    THEN 'RESISTANCE'
                WHEN sub.total >= 3 THEN 'MIXED'
                ELSE 'UNKNOWN'
            END
        FROM (
            SELECT
                level_price,
                COUNT(*) FILTER (WHERE outcome IN ('hold', 'break')) AS total,
                SUM(CASE WHEN outcome = 'hold' THEN 1 ELSE 0 END) AS holds,
                SUM(CASE WHEN outcome = 'break' THEN 1 ELSE 0 END) AS breaks,
                SUM(CASE WHEN approach = 'from_below' THEN 1 ELSE 0 END) AS support,
                SUM(CASE WHEN approach = 'from_above' THEN 1 ELSE 0 END) AS resistance,
                MAX(touched_at) AS last_ts
            FROM level_touches
            WHERE symbol = %s
              AND outcome IN ('hold', 'break')
            GROUP BY level_price
        ) sub
        WHERE pl.symbol = %s AND pl.level_price = sub.level_price
        """,
        (sym, sym),
    )
    conn.commit()
    cur.close()


def backfill_pending_outcomes(
    symbol: str,
    *,
    batch_size: int | None = None,
    max_rows: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Classify pending level_touches using ohlcv_candles forward windows.
    Returns counts: pending_seen, classified, skipped_no_bars, errors.
    """
    from config.settings import get_settings

    url = (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()
    if not url or is_database_url_placeholder(url):
        raise RuntimeError("DATABASE_URL is not configured")

    sym = symbol.upper()
    batch = batch_size or BACKFILL_BATCH_SIZE
    stats = {"pending_seen": 0, "classified": 0, "skipped_no_bars": 0, "errors": 0}

    conn = connect_psycopg2(url)
    try:
        while True:
            if max_rows is not None and stats["pending_seen"] >= max_rows:
                break

            limit = batch
            if max_rows is not None:
                limit = min(batch, max_rows - stats["pending_seen"])

            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, touched_at, price_at_touch, approach
                FROM level_touches
                WHERE symbol = %s
                  AND (outcome IS NULL OR outcome = 'pending')
                ORDER BY touched_at ASC
                LIMIT %s
                """,
                (sym, limit),
            )
            rows = cur.fetchall()
            cur.close()
            if not rows:
                break

            stats["pending_seen"] += len(rows)
            updates: list[tuple[Any, ...]] = []

            for touch_id, touched_at, price_at_touch, approach in rows:
                try:
                    result = classify_touch_row(
                        conn,
                        symbol=sym,
                        price_at_touch=float(price_at_touch),
                        approach=str(approach),
                        touched_at=_parse_ts(touched_at),
                    )
                    if result is None:
                        stats["skipped_no_bars"] += 1
                        continue
                    updates.append(
                        (
                            result.outcome,
                            result.price_move_after,
                            result.bars_to_outcome,
                            int(touch_id),
                        )
                    )
                except Exception as exc:
                    stats["errors"] += 1
                    logger.debug("%s touch_id=%s classify error: %s", sym, touch_id, exc)

            if dry_run:
                stats["classified"] += len(updates)
                continue

            if updates:
                cur = conn.cursor()
                cur.executemany(
                    """
                    UPDATE level_touches
                    SET outcome = %s,
                        price_move_after = %s,
                        bars_to_outcome = %s
                    WHERE id = %s
                    """,
                    updates,
                )
                conn.commit()
                cur.close()
                stats["classified"] += len(updates)

            if len(rows) < limit:
                break

        if not dry_run and stats["classified"] > 0:
            reaggregate_price_levels(conn, sym)
            logger.info("%s: reaggregated price_levels after backfill", sym)
    finally:
        conn.close()

    return stats


def drain_stale_pending_for_symbol(
    system: Any,
    *,
    batch_size: int | None = None,
) -> int:
    """
    Classify DB pending touches older than OUTCOME_WINDOW for one symbol.
    Uses system.update_outcome so live price_levels stay incrementally correct.
    """
    from ml.features.level_intelligence import _db_available, _get_conn

    if not _db_available():
        return 0

    sym = system.symbol.upper()
    batch = batch_size or LIVE_DRAIN_BATCH_SIZE
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=OUTCOME_WINDOW + 1)
    classified = 0

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, touched_at, price_at_touch, approach
            FROM level_touches
            WHERE symbol = %s
              AND (outcome IS NULL OR outcome = 'pending')
              AND touched_at < %s
            ORDER BY touched_at ASC
            LIMIT %s
            """,
            (sym, cutoff, batch),
        )
        rows = cur.fetchall()
        cur.close()

        for touch_id, touched_at, price_at_touch, approach in rows:
            result = classify_touch_row(
                conn,
                symbol=sym,
                price_at_touch=float(price_at_touch),
                approach=str(approach),
                touched_at=_parse_ts(touched_at),
            )
            if result is None:
                continue
            system.update_outcome(
                int(touch_id),
                result.outcome,
                result.price_move_after,
                result.bars_to_outcome,
            )
            classified += 1
    finally:
        conn.close()

    if classified:
        logger.info("%s: drained %d stale pending touch outcomes from DB", sym, classified)
    return classified
