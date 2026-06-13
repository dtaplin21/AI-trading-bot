"""
trading_mcp/tools/levels.py

MCP tool implementations for level intelligence.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def _connect():
    import psycopg2

    dsn = os.getenv("DATABASE_URL", "")
    ssl = os.getenv("DATABASE_SSL_DISABLE", "false").lower() == "true"
    if ssl:
        dsn = dsn.replace("?sslmode=require", "").replace("sslmode=require", "")
        return psycopg2.connect(dsn, sslmode="disable")
    return psycopg2.connect(dsn)


async def get_level_watchlist(symbol: str) -> str:
    """Return actionable watchlist rows for a symbol from the DB."""
    try:
        import psycopg2.extras

        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                w.level_price,
                w.role,
                w.entry_side,
                w.hold_rate,
                w.touch_count,
                w.strength_score,
                p.optimal_tp_pct,
                p.optimal_sl_pct,
                p.optimal_rr,
                p.expected_value_pct
            FROM level_watchlist w
            LEFT JOIN price_levels p
                ON w.symbol = p.symbol AND w.level_price = p.level_price
            WHERE w.symbol = %s AND w.is_active = TRUE
            ORDER BY p.expected_value_pct DESC NULLS LAST
            LIMIT 20
        """,
            (symbol.upper(),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return json.dumps({"symbol": symbol.upper(), "levels": rows}, indent=2, default=str)
    except Exception as e:
        logger.error("get_level_watchlist failed: %s", e)
        return json.dumps({"error": str(e)})


async def check_level_gate(symbol: str, price: float) -> str:
    """Check whether a given price would pass the level entry gate."""
    try:
        from pipeline.level_entry_gate import LevelEntryGate

        gate = LevelEntryGate(symbol=symbol.upper())
        setup = gate.check(current_price=price)
        if setup is None:
            return json.dumps(
                {
                    "symbol": symbol.upper(),
                    "price": price,
                    "passed": False,
                    "reason": "No qualified level within proximity",
                }
            )
        return json.dumps(
            {
                "symbol": symbol.upper(),
                "price": price,
                "passed": True,
                "level_price": setup.level_price,
                "entry_side": setup.entry_side,
                "entry_price": setup.entry_price,
                "target_price": setup.target_price,
                "stop_price": setup.stop_price,
                "tp_pct": setup.optimal_tp_pct,
                "sl_pct": setup.optimal_sl_pct,
                "ev_pct": setup.expected_value_pct,
                "touch_count": setup.touch_count,
                "hold_rate": setup.hold_rate,
            },
            indent=2,
        )
    except Exception as e:
        logger.error("check_level_gate failed: %s", e)
        return json.dumps({"error": str(e)})


async def get_recent_touches(symbol: str, limit: int = 20) -> str:
    """Return the last N level touches for a symbol."""
    try:
        import psycopg2.extras

        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                level_price,
                approach,
                touched_at,
                price_at_touch,
                volume_at_touch,
                outcome,
                price_move_after
            FROM level_touches
            WHERE symbol = %s
            ORDER BY touched_at DESC
            LIMIT %s
        """,
            (symbol.upper(), limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return json.dumps({"symbol": symbol.upper(), "touches": rows}, indent=2, default=str)
    except Exception as e:
        logger.error("get_recent_touches failed: %s", e)
        return json.dumps({"error": str(e)})
