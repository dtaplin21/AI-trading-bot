"""
api/services/level_progress.py

Fast-lane progress scoring — mirrors LevelEntryGate thresholds exactly.
Single source of truth: gate_thresholds() reads from the real gate instance.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from pipeline.level_entry_gate import LevelEntryGate, is_actionable_watchlist_row

logger = logging.getLogger(__name__)


@dataclass
class GateThresholds:
    min_touches: int
    min_hold_rate: float
    min_ev_pct: float
    tolerance_pct: float


def gate_thresholds() -> GateThresholds:
    """Pull thresholds directly from LevelEntryGate — never duplicate constants."""
    gate = LevelEntryGate(symbol="MES")
    return GateThresholds(
        min_touches=gate.min_touches,
        min_hold_rate=gate.min_hold_rate,
        min_ev_pct=gate.min_ev_pct,
        tolerance_pct=gate.tolerance_pct,
    )


def distance_pct(current: float, level: float) -> float:
    if level <= 0:
        return 999.0
    return abs(current - level) / level * 100.0


def score_row(
    row: dict,
    *,
    current_price: Optional[float],
    thresholds: GateThresholds,
) -> dict:
    """
    Score one level row against gate thresholds.
    Returns: progress_pct, bucket, blockers, checks, distance_pct
    """
    touches = int(row.get("touch_count") or 0)
    hold = float(row.get("hold_rate") or 0)
    ev = row.get("expected_value_pct")
    actionable = is_actionable_watchlist_row(row)

    checks: dict[str, bool] = {
        "watchlist_active": bool(row.get("is_active", True)),
        "actionable_exits": actionable,
        "touches_ok": touches >= thresholds.min_touches,
        "hold_ok": hold >= thresholds.min_hold_rate,
        "ev_ok": ev is not None and float(ev) >= thresholds.min_ev_pct,
        "at_price": False,
    }

    dist = None
    if current_price:
        dist = distance_pct(current_price, float(row["level_price"]))
        checks["at_price"] = dist <= thresholds.tolerance_pct

    weights = {
        "actionable_exits": 25,
        "touches_ok": 20,
        "hold_ok": 20,
        "ev_ok": 15,
        "at_price": 20,
    }
    progress = sum(w for k, w in weights.items() if checks.get(k))

    blockers = []
    if not checks["actionable_exits"]:
        blockers.append("exits incomplete (need TP/SL/EV/R:R)")
    if not checks["touches_ok"]:
        need = thresholds.min_touches - touches
        blockers.append(f"need {need} more touch(es) (have {touches}/{thresholds.min_touches})")
    if not checks["hold_ok"]:
        blockers.append(f"hold {hold:.0%} below {thresholds.min_hold_rate:.0%}")
    if not checks["ev_ok"]:
        blockers.append(f"EV {ev or 0:.3f}% below {thresholds.min_ev_pct:.3f}%")
    if dist is not None and not checks["at_price"]:
        blockers.append(f"price {dist:.2f}% away (need ≤{thresholds.tolerance_pct:.2f}%)")

    at_line_keys = ("actionable_exits", "touches_ok", "hold_ok", "ev_ok", "at_price")
    qualified_keys = ("actionable_exits", "touches_ok", "hold_ok", "ev_ok")

    if all(checks.get(k) for k in at_line_keys):
        bucket = "at_line"
    elif all(checks.get(k) for k in qualified_keys):
        bucket = "qualified"
    else:
        bucket = "building"

    return {
        "progress_pct": progress,
        "bucket": bucket,
        "blockers": blockers,
        "checks": checks,
        "distance_pct": dist,
    }


def _database_url() -> str | None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url or is_database_url_placeholder(url):
        return None
    return url


def load_all_level_rows(symbol: str) -> list[dict]:
    """
    Load all price_levels rows for a symbol, left-joined with watchlist.
    Includes levels not yet on the watchlist (building bucket).
    """
    url = _database_url()
    if not url:
        return []

    import psycopg2.extras

    try:
        conn = connect_psycopg2(url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                p.level_price,
                p.touch_count,
                p.hold_rate,
                p.role,
                p.optimal_tp_pct,
                p.optimal_sl_pct,
                p.optimal_rr,
                p.expected_value_pct,
                p.exit_win_rate,
                w.entry_side,
                COALESCE(w.is_active, false) AS is_active,
                w.strength_score
            FROM price_levels p
            LEFT JOIN level_watchlist w
                ON p.symbol = w.symbol AND p.level_price = w.level_price
            WHERE p.symbol = %s
            ORDER BY p.touch_count DESC, p.expected_value_pct DESC NULLS LAST
            LIMIT 25
            """,
            (symbol.upper(),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error("load_all_level_rows failed for %s: %s", symbol, e)
        return []


def load_recent_touches(limit: int = 15) -> list[dict]:
    """Recent level touches across all symbols."""
    url = _database_url()
    if not url:
        return []

    import psycopg2.extras

    try:
        conn = connect_psycopg2(url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                symbol,
                level_price,
                approach,
                touched_at,
                price_at_touch,
                outcome
            FROM level_touches
            ORDER BY touched_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error("load_recent_touches failed: %s", e)
        return []


def pick_closest(rows: list[dict]) -> Optional[dict]:
    """Return the row with the smallest distance_pct."""
    valid = [r for r in rows if r.get("distance_pct") is not None]
    if not valid:
        return None
    return min(valid, key=lambda r: r["distance_pct"])
