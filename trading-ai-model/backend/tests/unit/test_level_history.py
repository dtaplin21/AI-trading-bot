"""Unit tests for ml.features.level_history."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ml.features.cross_symbol_analysis import CrossSymbolAnalyzer
from ml.features.level_history import LEVEL_CONFIGS, Level, LevelHistoryTracker


def _oscillating_df(
    base: float = 1.0800,
    amplitude: float = 0.0100,
    n_bars: int = 400,
) -> pd.DataFrame:
    """Synthetic OHLCV that revisits the same support/resistance zones."""
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="5min")
    close = np.array(
        [base + amplitude * np.sin(i / 8.0) for i in range(n_bars)],
        dtype=np.float64,
    )
    high = close + amplitude * 0.15
    low = close - amplitude * 0.15
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n_bars, 1000.0),
        },
        index=idx,
    )


def test_fit_discovers_significant_levels():
    tracker = LevelHistoryTracker(symbol="EURUSD", asset_class="forex")
    tracker.fit(_oscillating_df())

    assert tracker._is_fitted
    assert len(tracker.levels) > 0
    top = tracker.levels[0]
    assert top.touch_count >= LEVEL_CONFIGS["forex"].min_touches
    assert 0.0 <= top.hold_rate <= 1.0
    assert top.strength_score >= 0.0


def test_get_features_returns_expected_keys():
    tracker = LevelHistoryTracker(symbol="EURUSD", asset_class="forex")
    df = _oscillating_df()
    tracker.fit(df)

    features = tracker.get_features(float(df["close"].iloc[-1]))
    assert "level_nearest_dist_pct" in features
    assert "level_zone_quality" in features
    assert features["level_nearest_touches"] >= 0


def test_get_features_unfitted_returns_zeros():
    tracker = LevelHistoryTracker(symbol="EURUSD", asset_class="forex")
    features = tracker.get_features(1.08)
    assert features["level_nearest_touches"] == 0
    assert features["level_zone_quality"] == 0.0


def test_save_and_load_roundtrip(tmp_path: Path):
    tracker = LevelHistoryTracker(symbol="EURUSD", asset_class="forex")
    tracker.fit(_oscillating_df())
    path = tmp_path / "EURUSD_levels.json"
    tracker.save(str(path))

    loaded = LevelHistoryTracker(symbol="EURUSD", asset_class="forex").load(str(path))
    assert loaded._is_fitted
    assert len(loaded.levels) == len(tracker.levels)
    assert loaded.levels[0].price == tracker.levels[0].price


def test_level_hold_rate_and_strength():
    lvl = Level(price=1.08, price_min=1.079, price_max=1.081, touch_count=10, hold_count=7)
    assert lvl.hold_rate == 0.7
    assert lvl.strength_score > 0.0
    assert lvl.is_significant


def test_cross_symbol_analyzer_accepts_level_history_tracker():
    trackers = {
        "EURUSD": LevelHistoryTracker("EURUSD", "forex").fit(_oscillating_df()),
        "GBPUSD": LevelHistoryTracker("GBPUSD", "forex").fit(
            _oscillating_df(base=1.2600)
        ),
    }
    analyzer = CrossSymbolAnalyzer().fit(trackers)
    assert analyzer.profile is not None
    assert analyzer.profile.n_levels_analyzed > 0
