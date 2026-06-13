"""Tests for level-first confirmation layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from agents.schemas import MethodOutput
from pipeline.confluence_report import ConfluenceReport, MethodVote
from pipeline.level_confirmation import (
    evaluate_level_confirmation,
    filter_confirm_method_outputs,
    regime_allows_level_reversal,
)
from pipeline.level_setup import LevelSetup


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


def _vote(method: str, direction: int, confidence: float = 0.8) -> MethodVote:
    return MethodVote(
        method_name=method,
        direction=direction,
        confidence=confidence,
        weight=1.0,
        weighted_score=direction * confidence,
        key_feature="x",
        is_proven=True,
    )


def _confluence(votes: list[MethodVote]) -> ConfluenceReport:
    return ConfluenceReport(
        symbol="EURUSD",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        regime="range",
        votes=votes,
        consensus_direction=1,
        confluence_score=0.7,
    )


def test_evaluate_level_confirmation_passes_when_aligned():
    votes = [
        _vote("candlestick", 1, 0.8),
        _vote("momentum", 1, 0.7),
        _vote("markov_state", 1, 0.5),
    ]
    result = evaluate_level_confirmation(_confluence(votes), _level())
    assert result.passed is True
    assert result.agreement >= 0.35


def test_evaluate_level_confirmation_fails_on_regime_veto():
    votes = [
        _vote("candlestick", 1, 0.8),
        _vote("markov_state", -1, 0.9),
    ]
    result = evaluate_level_confirmation(_confluence(votes), _level())
    assert result.passed is False
    assert result.reason == "regime_veto_markov_continuation"


def test_filter_drops_proximity_methods_when_far_from_level():
    outputs = [
        MethodOutput(method="candlestick", confidence=0.8, features={}),
        MethodOutput(method="harmonic", confidence=0.7, features={}),
    ]
    level = _level(level_price=1.0843)
    filtered = filter_confirm_method_outputs(outputs, level, current_price=1.1000)
    assert len(filtered) == 1
    assert filtered[0].method == "candlestick"


def test_regime_allows_when_markov_neutral():
    votes = [_vote("markov_state", 0, 0.9)]
    assert regime_allows_level_reversal(_confluence(votes), _level()) is True
