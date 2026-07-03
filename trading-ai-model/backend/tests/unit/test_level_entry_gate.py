"""Tests for LevelEntryGate and bar validators."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from pipeline.bar_validators import (
    approach_matches_entry_side,
    bar_touched_level,
    is_valid_bar_close,
)
from pipeline.level_entry_gate import (
    LevelEntryGate,
    is_actionable_watchlist_row,
    require_approach_side,
    require_bar_touch,
)


def _actionable_row(**overrides):
    row = {
        "level_price": 1.0843,
        "hold_rate": 0.72,
        "touch_count": 20,
        "strength_score": 0.8,
        "role": "SUPPORT",
        "entry_side": "BUY",
        "optimal_tp_pct": 0.28,
        "optimal_sl_pct": 0.12,
        "expected_value_pct": 0.18,
        "optimal_rr": 2.3,
        "exit_win_rate": 0.65,
    }
    row.update(overrides)
    return row


def test_is_valid_bar_close():
    assert is_valid_bar_close(1.0845)
    assert not is_valid_bar_close(0.0)
    assert not is_valid_bar_close(-1.0)
    assert not is_valid_bar_close(None)


def test_bar_touched_level_requires_range_intersection():
    level = 1.0843
    assert bar_touched_level(level, 1.0846, 1.0842, 0.15)
    assert not bar_touched_level(level, 1.0865, 1.0860, 0.15)


def test_approach_matches_entry_side():
    level = 1.0843
    assert approach_matches_entry_side("BUY", level, 1.0830, 0.15)
    assert not approach_matches_entry_side("BUY", level, 1.0850, 0.15)
    assert approach_matches_entry_side("SELL", level, 1.0855, 0.15)
    assert not approach_matches_entry_side("SELL", level, 1.0830, 0.15)


def test_is_actionable_watchlist_row_requires_exit_optimizer_fields():
    assert is_actionable_watchlist_row(_actionable_row()) is True
    assert is_actionable_watchlist_row(_actionable_row(optimal_rr=None)) is False
    assert is_actionable_watchlist_row(_actionable_row(expected_value_pct=0)) is False
    assert is_actionable_watchlist_row(_actionable_row(entry_side="EITHER")) is False
    assert is_actionable_watchlist_row(_actionable_row(exit_win_rate=0.40)) is False
    assert is_actionable_watchlist_row(_actionable_row(strength_score=0.40)) is False


def test_check_returns_none_when_watchlist_empty(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=pd.DataFrame(),
    ):
        assert gate.check(1.0843) is None


def test_check_returns_setup_when_bar_touches_level_from_below(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.15
    df = pd.DataFrame([_actionable_row()])
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        setup = gate.check(
            1.0845,
            bar_high=1.0846,
            bar_low=1.0842,
            prev_close=1.0830,
        )
    assert setup is not None
    assert setup.symbol == "EURUSD"
    assert setup.entry_side == "BUY"
    assert setup.optimal_rr == 2.3
    assert setup.exit_win_rate == 0.65


def test_check_rejects_invalid_close(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    assert gate.check(0.0, bar_high=0.0, bar_low=0.0, prev_close=1.08) is None


def test_check_rejects_close_only_without_bar_touch(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.15
    df = pd.DataFrame([_actionable_row()])
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        setup = gate.check(
            1.0845,
            bar_high=1.0870,
            bar_low=1.0865,
            prev_close=1.0830,
        )
    assert setup is None


def test_check_rejects_wrong_approach_side(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.5
    df = pd.DataFrame([_actionable_row()])
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        setup = gate.check(
            1.0845,
            bar_high=1.0846,
            bar_low=1.0842,
            prev_close=1.0850,
        )
    assert setup is None


def test_check_skips_row_without_optimal_rr(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.5
    df = pd.DataFrame([_actionable_row(optimal_rr=None)])
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        assert gate.check(1.0845, bar_high=1.0846, bar_low=1.0842, prev_close=1.0838) is None


def test_check_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("LEVEL_GATE_DISABLED", "true")
    gate = LevelEntryGate("EURUSD")
    assert gate.check(1.0843) is None


def test_require_bar_touch_defaults_true():
    assert require_bar_touch() is True


def test_require_approach_side_defaults_true():
    assert require_approach_side() is True
