"""Tests for fast-lane progress scoring."""

from __future__ import annotations

from api.services.level_progress import gate_thresholds, pick_closest, score_row


def _row(**overrides) -> dict:
    base = {
        "level_price": 1.0842,
        "touch_count": 7,
        "hold_rate": 0.62,
        "expected_value_pct": 0.11,
        "optimal_tp_pct": 0.28,
        "optimal_sl_pct": 0.12,
        "optimal_rr": 2.3,
        "exit_win_rate": 0.65,
        "entry_side": "BUY",
        "is_active": True,
    }
    base.update(overrides)
    return base


def test_at_line_when_all_checks_pass():
    t = gate_thresholds()
    price = _row()["level_price"] * (1 + t.tolerance_pct / 200)
    result = score_row(_row(), current_price=price, thresholds=t)
    assert result["bucket"] == "at_line"
    assert result["progress_pct"] == 100
    assert result["blockers"] == []


def test_qualified_when_price_too_far():
    t = gate_thresholds()
    price = _row()["level_price"] * 1.05
    result = score_row(_row(), current_price=price, thresholds=t)
    assert result["bucket"] == "qualified"
    assert result["checks"]["at_price"] is False
    assert any("away" in b for b in result["blockers"])


def test_building_when_touches_insufficient():
    t = gate_thresholds()
    result = score_row(_row(touch_count=2), current_price=None, thresholds=t)
    assert result["bucket"] == "building"
    assert result["checks"]["touches_ok"] is False
    assert any("touch" in b for b in result["blockers"])


def test_building_when_hold_rate_too_low():
    t = gate_thresholds()
    result = score_row(_row(hold_rate=0.30), current_price=None, thresholds=t)
    assert result["bucket"] == "building"
    assert result["checks"]["hold_ok"] is False


def test_building_when_exits_incomplete():
    t = gate_thresholds()
    result = score_row(_row(optimal_rr=None), current_price=None, thresholds=t)
    assert result["bucket"] == "building"
    assert result["checks"]["actionable_exits"] is False
    assert any("exits" in b for b in result["blockers"])


def test_progress_pct_increases_with_checks():
    t = gate_thresholds()
    result_no_price = score_row(_row(), current_price=None, thresholds=t)
    price = _row()["level_price"] * (1 + t.tolerance_pct / 200)
    result_with_price = score_row(_row(), current_price=price, thresholds=t)
    assert result_with_price["progress_pct"] > result_no_price["progress_pct"]


def test_pick_closest_returns_min_distance():
    rows = [
        {"symbol": "MES", "distance_pct": 0.31},
        {"symbol": "EURUSD", "distance_pct": 0.04},
        {"symbol": "BTCUSD", "distance_pct": 1.2},
    ]
    closest = pick_closest(rows)
    assert closest["symbol"] == "EURUSD"


def test_pick_closest_returns_none_for_empty():
    assert pick_closest([]) is None


def test_gate_thresholds_not_hardcoded():
    """Thresholds come from real gate — confirm they're reasonable."""
    t = gate_thresholds()
    assert t.min_touches >= 1
    assert 0 < t.min_hold_rate < 1
    assert t.tolerance_pct > 0
