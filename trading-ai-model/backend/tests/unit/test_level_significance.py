"""Tests for LevelSignificanceAnalyzer."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.features.level_significance import (
    LevelRole,
    LevelSignificanceAnalyzer,
    SignificantLevel,
    analyze_symbol,
)


def _oscillating_df(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    base = 100.0
    close = base + 5.0 * np.sin(np.linspace(0, 24 * np.pi, n))
    high = close + 0.8
    low = close - 0.8
    open_ = close - 0.1
    volume = 1000 + (np.abs(np.sin(np.linspace(0, 12 * np.pi, n))) * 500)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_fit_finds_significant_levels():
    df = _oscillating_df()
    analyzer = LevelSignificanceAnalyzer("TSLA", "equity", min_hits=3)
    analyzer.fit(df)
    assert analyzer._is_fitted
    assert len(analyzer.levels) > 0
    assert analyzer.top5
    assert analyzer.levels[0].rank == 1
    assert analyzer.levels[0].total_hits >= 3


def test_get_features_returns_sig_keys():
    df = _oscillating_df()
    analyzer = LevelSignificanceAnalyzer("TSLA", "equity", min_hits=3).fit(df)
    features = analyzer.get_features(float(df["close"].iloc[-1]))
    assert "sig_nearest_hits" in features
    assert "sig_nearest_hold_rate" in features
    assert "sig_vol_impact" in features
    assert "sig_at_top5_level" in features


def test_save_and_load_preserves_role_and_volume_impact(tmp_path):
    level = SignificantLevel(
        price=100.0,
        rank=1,
        total_hits=10,
        support_hits=6,
        resistance_hits=2,
        support_breaks=1,
        resistance_breaks=1,
        hold_count=8,
        break_count=2,
        hold_rate=0.8,
        _cached_role=LevelRole.SUPPORT,
        _cached_volume_impact="High volume → reversal",
        _cached_role_confidence=0.7,
        _cached_avg_volume_ratio=1.4,
        _cached_high_vol_hold_rate=0.75,
        _cached_low_vol_hold_rate=0.5,
    )
    analyzer = LevelSignificanceAnalyzer("TSLA", "equity")
    analyzer.levels = [level]
    analyzer.top5 = [level]
    analyzer._is_fitted = True

    path = tmp_path / "significance_latest.json"
    analyzer.save(str(path))

    loaded = LevelSignificanceAnalyzer("TSLA", "equity").load(str(path))
    assert loaded.levels[0].role == LevelRole.SUPPORT
    assert loaded.levels[0].volume_impact == "High volume → reversal"
    assert loaded.get_features(100.0)["sig_is_support"] == 1


def test_analyze_symbol_helper():
    analyzer = analyze_symbol("MES", "futures", _oscillating_df(), print_report=False)
    assert analyzer._is_fitted
