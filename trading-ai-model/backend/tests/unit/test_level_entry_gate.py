"""Tests for LevelEntryGate."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from pipeline.level_entry_gate import LevelEntryGate
from pipeline.level_setup import LevelSetup


def test_check_returns_none_when_watchlist_empty(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=pd.DataFrame(),
    ):
        assert gate.check(1.0843) is None


def test_check_returns_setup_when_price_at_level(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    gate = LevelEntryGate("EURUSD")
    gate.tolerance_pct = 0.5
    df = pd.DataFrame(
        [
            {
                "level_price": 1.0843,
                "hold_rate": 0.72,
                "touch_count": 20,
                "strength_score": 0.8,
                "role": "SUPPORT",
                "entry_side": "BUY",
                "optimal_tp_pct": 0.28,
                "optimal_sl_pct": 0.12,
                "expected_value_pct": 0.18,
            }
        ]
    )
    with patch(
        "ml.features.trade_exit_optimizer.TradeExitOptimizer.get_watchlist_with_exits",
        return_value=df,
    ):
        setup = gate.check(1.0845)
    assert isinstance(setup, LevelSetup)
    assert setup.symbol == "EURUSD"
    assert setup.entry_side == "BUY"


def test_check_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("LEVEL_GATE_DISABLED", "true")
    gate = LevelEntryGate("EURUSD")
    assert gate.check(1.0843) is None
