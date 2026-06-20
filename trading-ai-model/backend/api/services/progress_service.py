"""
api/services/progress_service.py

Builds the GET /progress payload.
Reads watchlist + price_levels from DB, scores every level,
buckets into at_line / qualified / building.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from api.services.level_progress import (
    _database_url,
    gate_thresholds,
    load_all_level_rows,
    load_recent_touches,
    pick_closest,
    score_row,
)
from config.symbols import DEFAULT_WATCHER_SYMBOLS
from data.storage.pg_connect import connect_psycopg2

logger = logging.getLogger(__name__)

PROGRESS_SYMBOLS = list(DEFAULT_WATCHER_SYMBOLS)


def _get_last_price(symbol: str) -> Optional[float]:
    """
    Pull the most recent close from ohlcv_candles.
    Returns None if unavailable — progress still shows without live price.
    """
    url = _database_url()
    if not url:
        return None

    timeframe = os.getenv("WATCHLIST_PRIMARY_TF", "5m")
    try:
        conn = connect_psycopg2(url)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT close FROM ohlcv_candles
            WHERE symbol = %s AND timeframe = %s
            ORDER BY time DESC LIMIT 1
            """,
            (symbol.upper(), timeframe),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return float(row[0]) if row else None
    except Exception as exc:
        logger.debug("_get_last_price failed for %s: %s", symbol, exc)
        return None


def build_progress_payload() -> dict:
    thresholds = gate_thresholds()
    at_line, qualified, building = [], [], []

    if not _database_url():
        logger.warning(
            "progress: DATABASE_URL not configured — returning empty progress buckets"
        )

    for symbol in PROGRESS_SYMBOLS:
        current_price = _get_last_price(symbol)
        rows = load_all_level_rows(symbol)

        for row in rows:
            scored = score_row(row, current_price=current_price, thresholds=thresholds)
            entry = {
                "symbol": symbol.upper(),
                "level_price": row.get("level_price"),
                "role": row.get("role"),
                "entry_side": row.get("entry_side"),
                "touch_count": row.get("touch_count"),
                "touch_target": thresholds.min_touches,
                "hold_rate": row.get("hold_rate"),
                "hold_target": thresholds.min_hold_rate,
                "expected_value_pct": row.get("expected_value_pct"),
                "optimal_rr": row.get("optimal_rr"),
                "optimal_tp_pct": row.get("optimal_tp_pct"),
                "optimal_sl_pct": row.get("optimal_sl_pct"),
                "exit_win_rate": row.get("exit_win_rate"),
                "is_active": row.get("is_active", False),
                "current_price": current_price,
                **scored,
            }
            if scored["bucket"] == "at_line":
                at_line.append(entry)
            elif scored["bucket"] == "qualified":
                qualified.append(entry)
            else:
                building.append(entry)

    closest = pick_closest(qualified + building)

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "min_touches": thresholds.min_touches,
            "min_hold_rate": thresholds.min_hold_rate,
            "min_ev_pct": thresholds.min_ev_pct,
            "tolerance_pct": thresholds.tolerance_pct,
        },
        "summary": {
            "at_line": len(at_line),
            "qualified": len(qualified),
            "building": len(building),
            "closest": closest,
        },
        "at_line": sorted(at_line, key=lambda x: -x["progress_pct"]),
        "qualified": sorted(qualified, key=lambda x: x.get("distance_pct") or 999),
        "building": sorted(building, key=lambda x: -x["progress_pct"]),
        "recent_touches": load_recent_touches(limit=15),
    }
