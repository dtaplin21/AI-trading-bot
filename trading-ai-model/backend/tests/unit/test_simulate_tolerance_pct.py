"""Unit tests for tolerance simulation (no DB)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.simulate_tolerance_pct import (
    GateParams,
    _actionable_rejections,
    _closest_approach_pct,
    _filter_valid_closes,
    pick_gate_level,
)


def _row(
    price: float,
    *,
    ev: float = 0.5,
    touches: int = 10,
    hold: float = 0.6,
    side: str = "BUY",
) -> dict:
    return {
        "level_price": price,
        "entry_side": side,
        "role": "SUPPORT",
        "hold_rate": hold,
        "touch_count": touches,
        "optimal_tp_pct": 0.4,
        "optimal_sl_pct": 0.2,
        "optimal_rr": 2.0,
        "expected_value_pct": ev,
        "exit_win_rate": 0.55,
    }


def test_picks_highest_ev_not_closest():
    watchlist = [
        _row(100.0, ev=0.3),
        _row(100.5, ev=0.9),
    ]
    best = pick_gate_level(100.2, watchlist, tolerance_pct=0.5, gate=GateParams())
    assert best is not None
    assert float(best["level_price"]) == 100.5


def test_outside_tolerance_returns_none():
    watchlist = [_row(100.0)]
    assert pick_gate_level(101.0, watchlist, tolerance_pct=0.15, gate=GateParams()) is None


def test_hold_rate_filter():
    watchlist = [_row(100.0, hold=0.40)]
    assert pick_gate_level(100.0, watchlist, tolerance_pct=0.15, gate=GateParams()) is None


def test_filter_valid_closes_drops_zeros():
    valid, skipped = _filter_valid_closes([1.1, 0.0, -1.0, 1.2, 0.0])
    assert valid == [1.1, 1.2]
    assert skipped == 3


def test_closest_approach_pct():
    levels = [_row(100.0), _row(105.0)]
    assert _closest_approach_pct([102.0], levels) == 2.0


def test_actionable_rejections_rr_and_either():
    gate = GateParams(min_rr=1.0)
    rows = [
        _row(100.0, side="EITHER"),
        _row(101.0, ev=0.5),
        {**_row(102.0), "optimal_rr": 0.8},
    ]
    counts = _actionable_rejections(rows, gate)
    assert counts["either_or_unknown_side"] == 1
    assert counts["rr_below_min"] == 1
    assert counts["actionable"] == 1
