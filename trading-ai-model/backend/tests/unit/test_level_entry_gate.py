"""Tests for LevelEntryGate."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from pipeline.level_entry_gate import (
    LevelEntryGate,
    is_actionable_watchlist_row,
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


def test_is_actionable_watchlist_row_requires_exit_optimizer_fields():
    assert is_actionable_watchlist_row(_actionable_row()) is True
    assert is_actionable_watchlist_row(_actionable_row(optimal_rr=None)) is False
    assert is_actionable_watchlist_row(_actionable_row(expected_value_pct=0)) is False
    assert is_actionable_watchlist_row(_actionable_row(entry_side="EITHER")) is False


def test_check_returns_none_when_watchlist_empty(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=pd.DataFrame(),
    ):
        assert gate.check(1.0843) is None


def test_check_returns_setup_when_price_at_actionable_level(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.5
    df = pd.DataFrame([_actionable_row()])
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        setup = gate.check(1.0845)
    assert setup is not None
    assert setup.symbol == "EURUSD"
    assert setup.entry_side == "BUY"
    assert setup.optimal_rr == 2.3
    assert setup.exit_win_rate == 0.65


def test_check_skips_row_without_optimal_rr(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.5
    df = pd.DataFrame([_actionable_row(optimal_rr=None)])
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        assert gate.check(1.0845) is None


def test_check_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("LEVEL_GATE_DISABLED", "true")
    gate = LevelEntryGate("EURUSD")
    assert gate.check(1.0843) is None
