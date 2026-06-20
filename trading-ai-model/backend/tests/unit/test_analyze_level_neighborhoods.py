"""Unit tests for level neighborhood analysis (no DB)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analyze_level_neighborhoods import compute_neighborhood


def _level(price: float, role: str = "SUPPORT", hold_rate: float = 0.5) -> dict:
    return {
        "level_price": price,
        "role": role,
        "hold_rate": hold_rate,
        "touch_count": 10,
        "strength_score": 0.7,
    }


def test_compute_neighborhood_neighbors_above_and_below():
    levels = [_level(100 + i, hold_rate=0.4 + i * 0.01) for i in range(5)]
    profile = compute_neighborhood(levels, idx=2, symbol="MES", k=30)

    assert profile.symbol == "MES"
    assert profile.level_price == 102.0
    assert profile.neighbors_below_count == 2
    assert profile.neighbors_above_count == 2
    assert profile.neighbor_avg_hold_rate > 0


def test_compute_neighborhood_counts_mixed_neighbors():
    levels = [
        _level(100, "SUPPORT", 0.6),
        _level(101, "MIXED", 0.8),
        _level(102, "RESISTANCE", 0.5),
        _level(103, "MIXED", 0.7),
    ]
    profile = compute_neighborhood(levels, idx=2, symbol="BTCUSD", k=30)

    assert profile.either_neighbors_count == 2
    assert profile.either_neighbors_avg_hold_rate == 0.75


def test_compute_neighborhood_edge_level_has_fewer_neighbors():
    levels = [_level(10 + i) for i in range(3)]
    profile = compute_neighborhood(levels, idx=0, symbol="MES", k=30)

    assert profile.neighbors_below_count == 0
    assert profile.neighbors_above_count == 2
