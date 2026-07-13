"""
Resolve MIXED / EITHER watchlist entry_side from classified touch history.

Uses approach-specific hold rates:
  from_below → BUY  (support bounce)
  from_above → SELL (resistance rejection)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from ml.features.level_intelligence import wilson_lower_bound

logger = logging.getLogger("entry_side_resolver")

MIN_APPROACH_TOUCHES = int(os.getenv("ENTRY_SIDE_MIN_APPROACH_TOUCHES", "5"))
MIN_APPROACH_HOLD = float(os.getenv("ENTRY_SIDE_MIN_HOLD", "0.51"))


@dataclass(frozen=True)
class ApproachBreakdown:
    from_below_total: int = 0
    from_below_holds: int = 0
    from_above_total: int = 0
    from_above_holds: int = 0
    avg_move_below_hold: float = 0.0
    avg_move_above_hold: float = 0.0

    @property
    def from_below_hold_rate(self) -> float:
        if self.from_below_total < 1:
            return 0.0
        return self.from_below_holds / self.from_below_total

    @property
    def from_above_hold_rate(self) -> float:
        if self.from_above_total < 1:
            return 0.0
        return self.from_above_holds / self.from_above_total


def load_approach_breakdown(conn, symbol: str, level_price: float) -> ApproachBreakdown:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            approach,
            COUNT(*) FILTER (WHERE outcome IN ('hold', 'break')) AS total,
            SUM(CASE WHEN outcome = 'hold' THEN 1 ELSE 0 END) AS holds,
            AVG(CASE WHEN outcome = 'hold' THEN price_move_after END) AS avg_hold_move
        FROM level_touches
        WHERE symbol = %s
          AND level_price = %s
          AND outcome IN ('hold', 'break')
        GROUP BY approach
        """,
        (symbol.upper(), float(level_price)),
    )
    rows = cur.fetchall()
    cur.close()

    below_total = below_holds = above_total = above_holds = 0
    avg_below = avg_above = 0.0
    for approach, total, holds, avg_move in rows:
        total_i = int(total or 0)
        holds_i = int(holds or 0)
        if approach == "from_below":
            below_total, below_holds = total_i, holds_i
            avg_below = float(avg_move or 0.0)
        elif approach == "from_above":
            above_total, above_holds = total_i, holds_i
            avg_above = float(avg_move or 0.0)

    return ApproachBreakdown(
        from_below_total=below_total,
        from_below_holds=below_holds,
        from_above_total=above_total,
        from_above_holds=above_holds,
        avg_move_below_hold=avg_below,
        avg_move_above_hold=avg_above,
    )


def resolve_entry_side(
    role: str,
    breakdown: ApproachBreakdown,
    *,
    min_approach_touches: int | None = None,
    min_approach_hold: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Return (BUY|SELL|EITHER, intel dict) from role + approach-specific stats.
    """
    min_touches = min_approach_touches if min_approach_touches is not None else MIN_APPROACH_TOUCHES
    min_hold = min_approach_hold if min_approach_hold is not None else MIN_APPROACH_HOLD
    role_u = str(role or "UNKNOWN").upper()

    intel: dict[str, Any] = {
        "role": role_u,
        "from_below_total": breakdown.from_below_total,
        "from_below_hold_rate": round(breakdown.from_below_hold_rate, 4),
        "from_above_total": breakdown.from_above_total,
        "from_above_hold_rate": round(breakdown.from_above_hold_rate, 4),
        "avg_move_below_hold": round(breakdown.avg_move_below_hold, 4),
        "avg_move_above_hold": round(breakdown.avg_move_above_hold, 4),
    }

    if role_u == "SUPPORT":
        intel["reason"] = "role_support"
        return "BUY", intel
    if role_u == "RESISTANCE":
        intel["reason"] = "role_resistance"
        return "SELL", intel

    buy_ok = (
        breakdown.from_below_total >= min_touches
        and breakdown.from_below_hold_rate >= min_hold
    )
    sell_ok = (
        breakdown.from_above_total >= min_touches
        and breakdown.from_above_hold_rate >= min_hold
    )

    buy_strength = wilson_lower_bound(
        breakdown.from_below_hold_rate, breakdown.from_below_total
    )
    sell_strength = wilson_lower_bound(
        breakdown.from_above_hold_rate, breakdown.from_above_total
    )
    intel["buy_strength"] = round(buy_strength, 4)
    intel["sell_strength"] = round(sell_strength, 4)

    if buy_ok and not sell_ok:
        intel["reason"] = "approach_below_only"
        return "BUY", intel
    if sell_ok and not buy_ok:
        intel["reason"] = "approach_above_only"
        return "SELL", intel
    if buy_ok and sell_ok:
        if buy_strength > sell_strength:
            intel["reason"] = "approach_strength_buy"
            return "BUY", intel
        if sell_strength > buy_strength:
            intel["reason"] = "approach_strength_sell"
            return "SELL", intel
        if breakdown.avg_move_below_hold >= breakdown.avg_move_above_hold:
            intel["reason"] = "approach_move_tie_buy"
            return "BUY", intel
        intel["reason"] = "approach_move_tie_sell"
        return "SELL", intel

    intel["reason"] = "insufficient_approach_data"
    return "EITHER", intel


def resolve_entry_side_for_level(
    conn,
    symbol: str,
    level_price: float,
    role: str,
) -> tuple[str, dict[str, Any]]:
    breakdown = load_approach_breakdown(conn, symbol, level_price)
    side, intel = resolve_entry_side(role, breakdown)
    intel["level_price"] = float(level_price)
    return side, intel


def sync_watchlist_entry_sides(conn, symbol: str) -> int:
    """Update entry_side on active watchlist rows using classified touch history."""
    sym = symbol.upper()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT level_price, role, entry_side
        FROM level_watchlist
        WHERE symbol = %s AND is_active = TRUE
        """,
        (sym,),
    )
    rows = cur.fetchall()
    updated = 0

    for level_price, role, current_side in rows:
        side, intel = resolve_entry_side_for_level(conn, sym, float(level_price), str(role))
        if side == str(current_side or "").upper():
            continue
        cur.execute(
            """
            UPDATE level_watchlist
            SET entry_side = %s
            WHERE symbol = %s AND level_price = %s
            """,
            (side, sym, float(level_price)),
        )
        updated += 1
        if side != "EITHER":
            logger.info(
                "%s @ %.5f: entry_side %s → %s (%s)",
                sym,
                float(level_price),
                current_side,
                side,
                intel.get("reason"),
            )

    conn.commit()
    cur.close()
    return updated
