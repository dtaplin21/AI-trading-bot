"""Tests for LevelIntelligenceSystem (no live DB required)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ml.features.level_intelligence import (
    LevelIntelligenceSystem,
    PriceLevel,
    TouchSnapshot,
    build_snapshot,
    wilson_lower_bound,
)


def test_wilson_lower_bound_conservative_on_small_sample():
    assert wilson_lower_bound(1.0, 3) < 1.0
    assert wilson_lower_bound(0.0, 0) == 0.0
    assert 0.4 < wilson_lower_bound(0.7, 20) < 0.7


def test_price_level_classify_role():
    support = PriceLevel("TSLA", 100.0, 99.5, 100.5, support_count=7, resistance_count=2)
    assert support.classify_role() == "SUPPORT"
    resistance = PriceLevel("TSLA", 100.0, 99.5, 100.5, support_count=1, resistance_count=8)
    assert resistance.classify_role() == "RESISTANCE"
    mixed = PriceLevel("TSLA", 100.0, 99.5, 100.5, support_count=4, resistance_count=4)
    assert mixed.classify_role() == "MIXED"


def test_touch_snapshot_to_db_row_rounds_values():
    snap = TouchSnapshot(
        symbol="TSLA",
        level_price=360.0,
        touched_at=datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc),
        price_at_touch=360.012345,
        approach="from_below",
        outcome="pending",
        volume_at_touch=12345.678,
        volume_ratio=1.234,
        rsi_14=55.555,
        macd_histogram=0.0012345,
        atr_pct=0.01234,
        bb_position=0.5678,
        session="NEW_YORK",
    )
    row = snap.to_db_row()
    assert row["symbol"] == "TSLA"
    assert row["approach"] == "from_below"
    assert row["outcome"] == "pending"
    assert row["price_at_touch"] == pytest.approx(360.012345, rel=1e-5)


def _touch_df(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = 100.0 + np.zeros(n)
    close[-6:-1] = [101.0, 100.5, 100.1, 99.95, 100.0]
    close[-1] = 100.0
    high = close + 0.15
    low = close - 0.15
    open_ = close - 0.02
    volume = np.full(n, 1000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_build_snapshot_populates_indicators():
    df = _touch_df()
    snap = build_snapshot("TSLA", df, len(df) - 1, "from_above")
    assert snap.symbol == "TSLA"
    assert snap.approach == "from_above"
    assert snap.outcome == "pending"
    assert 0 <= snap.bb_position <= 2
    assert snap.volume_ratio > 0


def test_get_features_without_db_returns_defaults(monkeypatch):
    monkeypatch.setattr(
        "ml.features.level_intelligence._db_available",
        lambda: False,
    )
    system = LevelIntelligenceSystem("TSLA", "equity")
    features = system.get_features(100.0)
    assert features["li_found"] == 0.0
    assert features["li_probability"] == 0.3
    assert "li_touch_count" in features


def test_detect_touch_from_above(monkeypatch):
    monkeypatch.setattr(
        "ml.features.level_intelligence._db_available",
        lambda: False,
    )
    system = LevelIntelligenceSystem("TSLA", "equity")
    df = _touch_df()
    touch = system._detect_touch(df, len(df) - 1)
    assert touch is not None
    approach, level = touch
    assert approach in ("from_above", "from_below")
    assert level > 0


def test_get_probability_without_db():
    system = LevelIntelligenceSystem("TSLA", "equity")
    prob = system.get_probability(100.0)
    assert prob["found"] is False
    assert prob["probability"] == 0.3
