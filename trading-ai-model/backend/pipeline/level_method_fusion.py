"""Fuse level intelligence with ML and method confluence."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from pipeline.confluence_report import ConfluenceReport
from pipeline.level_setup import LevelSetup
from pipeline.schemas import TradeAction, TradePlan


def level_direction(level_setup: LevelSetup) -> int:
    """+1 long/BUY, -1 short/SELL."""
    return 1 if level_setup.entry_side.upper() == "BUY" else -1


def method_agreement_score(confluence: ConfluenceReport, level_setup: LevelSetup) -> float:
    """
    Fraction of weighted method confidence aligned with the level entry side.
    Methods confirm the level thesis — they do not pick direction alone.
    """
    want = level_direction(level_setup)
    agree = 0.0
    total = 0.0
    for vote in confluence.votes:
        if vote.direction == 0:
            continue
        weight = abs(vote.weight) * max(vote.confidence, 0.0)
        total += weight
        if vote.direction == want:
            agree += weight
    if total <= 0:
        if confluence.consensus_direction == want:
            return min(1.0, confluence.confluence_score)
        if confluence.consensus_direction == 0:
            return 0.5
        return 0.0
    return agree / total


def fused_level_probability(level_setup: LevelSetup, ml_p: float) -> float:
    """Blend Wilson hold rate (P_base) with ML reversal probability."""
    ml_weight = float(os.getenv("LEVEL_ML_BLEND_WEIGHT", "0.40"))
    ml_weight = max(0.0, min(1.0, ml_weight))
    base = float(level_setup.hold_rate)
    return round((1.0 - ml_weight) * base + ml_weight * ml_p, 4)


def min_method_agreement() -> float:
    return float(os.getenv("LEVEL_MIN_METHOD_AGREEMENT", "0.35"))


def plan_from_level_setup(level_setup: LevelSetup, timeframe: str = "5m") -> TradePlan:
    """Build TradePlan directly from actionable watchlist row — no methods/ML."""
    action = (
        TradeAction.ENTER_LONG
        if level_setup.entry_side.upper() == "BUY"
        else TradeAction.ENTER_SHORT
    )
    win_pct = (
        level_setup.exit_win_rate * 100
        if level_setup.exit_win_rate <= 1
        else level_setup.exit_win_rate
    )
    return TradePlan(
        symbol=level_setup.symbol,
        timeframe=timeframe,
        timestamp=datetime.now(tz=timezone.utc),
        action=action,
        entry_price=level_setup.entry_price,
        stop_loss=level_setup.stop_price,
        take_profit=level_setup.target_price,
        plan_confidence=level_setup.hold_rate,
        plan_ev=level_setup.expected_value_pct,
        plan_notes=(
            f"Level fast lane | role={level_setup.role} EV={level_setup.expected_value_pct:.3f}% "
            f"RR={level_setup.optimal_rr:.1f} win={win_pct:.1f}% hits={level_setup.touch_count}"
        ),
    )


def apply_level_to_plan(plan: TradePlan, level_setup: LevelSetup) -> TradePlan:
    """Override planner prices with DB level entry / TP / SL."""
    action = (
        TradeAction.ENTER_LONG
        if level_setup.entry_side.upper() == "BUY"
        else TradeAction.ENTER_SHORT
    )
    notes = plan.plan_notes or ""
    level_note = (
        f"Level-driven exits | EV={level_setup.expected_value_pct:.3f}% "
        f"touches={level_setup.touch_count}"
    )
    return plan.model_copy(
        update={
            "action": action,
            "entry_price": level_setup.entry_price,
            "stop_loss": level_setup.stop_price,
            "take_profit": level_setup.target_price,
            "plan_notes": f"{notes} | {level_note}".strip(" |"),
        }
    )
