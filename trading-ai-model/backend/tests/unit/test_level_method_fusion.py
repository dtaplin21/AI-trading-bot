"""Tests for level + method fusion helpers."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from pipeline.confluence_report import ConfluenceReport, MethodVote
from pipeline.level_method_fusion import (
    apply_level_to_plan,
    fused_level_probability,
    method_agreement_score,
)
from pipeline.level_setup import LevelSetup
from pipeline.schemas import TradeAction, TradePlan


def _level(**kwargs: Any) -> LevelSetup:
    base = LevelSetup(
        symbol="EURUSD",
        level_price=1.0843,
        entry_price=1.0843,
        target_price=1.0873,
        stop_price=1.0820,
        entry_side="BUY",
        hold_rate=0.72,
        touch_count=20,
        optimal_tp_pct=0.28,
        optimal_sl_pct=0.12,
        expected_value_pct=0.18,
    )
    return replace(base, **kwargs) if kwargs else base


def _confluence(votes: list[MethodVote]) -> ConfluenceReport:
    return ConfluenceReport(
        symbol="EURUSD",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        regime="trend",
        votes=votes,
        consensus_direction=1,
        confluence_score=0.7,
    )


def test_method_agreement_all_aligned():
    votes = [
        MethodVote(
            method_name="fib",
            direction=1,
            confidence=0.8,
            weight=1.0,
            weighted_score=0.8,
            key_feature="zone",
            is_proven=True,
        ),
        MethodVote(
            method_name="harmonic",
            direction=1,
            confidence=0.6,
            weight=1.0,
            weighted_score=0.6,
            key_feature="pattern",
            is_proven=True,
        ),
    ]
    score = method_agreement_score(_confluence(votes), _level())
    assert score == 1.0


def test_method_agreement_opposing():
    votes = [
        MethodVote(
            method_name="fib",
            direction=-1,
            confidence=0.9,
            weight=1.0,
            weighted_score=-0.9,
            key_feature="zone",
            is_proven=True,
        ),
    ]
    score = method_agreement_score(_confluence(votes), _level(entry_side="BUY"))
    assert score == 0.0


def test_fused_level_probability_blend():
    fused = fused_level_probability(_level(hold_rate=0.80), ml_p=0.60)
    assert 0.60 < fused < 0.80


def test_apply_level_to_plan_overrides_prices():
    plan = TradePlan(
        symbol="EURUSD",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        action=TradeAction.DO_NOTHING,
        entry_price=1.0,
        stop_loss=0.9,
        take_profit=1.1,
    )
    level = _level()
    updated = apply_level_to_plan(plan, level)
    assert updated.action == TradeAction.ENTER_LONG
    assert updated.entry_price == level.entry_price
    assert updated.stop_loss == level.stop_price
    assert updated.take_profit == level.target_price
