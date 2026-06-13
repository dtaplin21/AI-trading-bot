"""Unit tests for ml.features.cross_symbol_analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ml.features.cross_symbol_analysis import (
    CrossSymbolAnalyzer,
    UniversalLevelProfile,
)


@dataclass
class _MockLevel:
    price: float
    touch_count: int
    hold_count: int
    break_count: int
    hold_rate: float
    strength_score: float


class _MockTracker:
    def __init__(self, symbol: str, asset_class: str, levels: list[_MockLevel]):
        self.symbol = symbol
        self.asset_class = asset_class
        self.levels = levels
        self._is_fitted = bool(levels)


def _make_trackers() -> dict:
    """Two symbols with enough levels for cross-symbol fit."""
    mes_levels = [
        _MockLevel(5200.0, 10, 7, 3, 0.70, 0.72),
        _MockLevel(5180.0, 8, 5, 3, 0.625, 0.65),
        _MockLevel(5150.0, 12, 9, 3, 0.75, 0.80),
        _MockLevel(5120.0, 6, 3, 3, 0.50, 0.48),
        _MockLevel(5100.0, 9, 6, 3, 0.667, 0.68),
    ]
    es_levels = [
        _MockLevel(5200.0, 11, 8, 3, 0.727, 0.74),
        _MockLevel(5185.0, 7, 4, 3, 0.571, 0.55),
        _MockLevel(5160.0, 10, 7, 3, 0.70, 0.71),
        _MockLevel(5140.0, 8, 5, 3, 0.625, 0.62),
        _MockLevel(5110.0, 5, 2, 3, 0.40, 0.38),
    ]
    eur_levels = [
        _MockLevel(1.0850, 9, 6, 3, 0.667, 0.66),
        _MockLevel(1.0800, 7, 5, 2, 0.714, 0.70),
        _MockLevel(1.0750, 6, 4, 2, 0.667, 0.64),
        _MockLevel(1.0700, 8, 5, 3, 0.625, 0.60),
    ]
    return {
        "MES": _MockTracker("MES", "futures", mes_levels),
        "ES": _MockTracker("ES", "futures", es_levels),
        "EURUSD": _MockTracker("EURUSD", "forex", eur_levels),
    }


def test_fit_builds_profile():
    analyzer = CrossSymbolAnalyzer()
    analyzer.fit(_make_trackers())

    assert analyzer._is_fitted
    assert analyzer.profile is not None
    assert analyzer.profile.n_symbols == 3
    assert analyzer.profile.n_levels_analyzed > 0
    assert analyzer.profile.mean_hold_rate > 0


def test_universal_strength_score_requires_min_touches():
    profile = UniversalLevelProfile(min_reliable_touches=5)
    assert profile.universal_strength_score(0.80, 4, 0.75) == 0.0
    assert profile.universal_strength_score(0.80, 8, 0.75) > 0


def test_get_features_returns_cross_symbol_keys():
    analyzer = CrossSymbolAnalyzer()
    analyzer.fit(_make_trackers())

    features = analyzer.get_features(
        symbol="MES",
        hold_rate=0.72,
        touch_count=10,
        strength=0.70,
        current_price=5200.0,
        all_trackers=_make_trackers(),
    )

    assert "cx_universal_score" in features
    assert "cx_correlated_confirmation" in features
    assert features["cx_has_confirmation"] == 1
    assert features["cx_correlated_confirmation"] >= 1


def test_get_features_unfitted_returns_empty():
    analyzer = CrossSymbolAnalyzer()
    features = analyzer.get_features("MES", 0.7, 10, 0.7)
    assert features["cx_universal_score"] == 0.0
    assert features["cx_hold_rate_percentile"] == 0.5


def test_save_and_load_roundtrip(tmp_path: Path):
    analyzer = CrossSymbolAnalyzer()
    analyzer.fit(_make_trackers())
    path = tmp_path / "cross_symbol_profile.json"
    analyzer.save(str(path))

    loaded = CrossSymbolAnalyzer().load(str(path))
    assert loaded._is_fitted
    assert loaded.profile is not None
    assert analyzer.profile is not None
    assert loaded.profile.n_levels_analyzed == analyzer.profile.n_levels_analyzed
    assert loaded.per_symbol.keys() == analyzer.per_symbol.keys()


def test_classify_level():
    profile = UniversalLevelProfile(
        strong_hold_rate_threshold=0.65,
        weak_hold_rate_threshold=0.45,
        strong_touch_threshold=8,
        min_reliable_touches=5,
        percentile_75_hold_rate=0.60,
    )
    assert profile.classify_level(0.70, 10) == "strong"
    assert profile.classify_level(0.40, 10) == "weak"
    assert profile.classify_level(0.55, 3) == "unproven"
