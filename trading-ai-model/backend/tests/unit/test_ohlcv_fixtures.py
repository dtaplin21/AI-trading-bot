"""Tests for synthetic OHLCV fixtures used in level-discovery integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from ml.features.level_history import LevelHistoryTracker
from tests.fixtures.ohlcv.synthetic import (
    EXPECTED_MES_SWING_CLUSTERS,
    MES_1M_CSV_PATH,
    load_mes_1m_csv,
    mes_discovery_ohlcv_1m,
    mes_discovery_ohlcv_5m,
)


def test_mes_1m_fixture_has_minimum_bars():
    df = mes_discovery_ohlcv_1m()
    assert len(df) >= 500
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.tz is not None


def test_mes_5m_fixture_has_minimum_bars():
    df = mes_discovery_ohlcv_5m()
    assert len(df) >= 500


def test_mes_1m_csv_roundtrip():
    assert MES_1M_CSV_PATH.is_file()
    df = load_mes_1m_csv()
    assert len(df) >= 500
    assert df["close"].min() < EXPECTED_MES_SWING_CLUSTERS[0] + 5
    assert df["close"].max() > EXPECTED_MES_SWING_CLUSTERS[2] - 5


def test_mes_fixture_produces_known_swing_clusters():
    tracker = LevelHistoryTracker(symbol="MES", asset_class="futures")
    tracker.fit(mes_discovery_ohlcv_5m())

    assert tracker.levels, "expected at least one clustered swing level"
    prices = sorted(level.price for level in tracker.levels)

    support, _, resistance = EXPECTED_MES_SWING_CLUSTERS
    assert any(abs(p - support) <= 2.0 for p in prices), prices
    assert any(abs(p - resistance) <= 2.0 for p in prices), prices


def test_mes_1m_rejects_too_few_bars():
    with pytest.raises(ValueError, match="at least 500"):
        mes_discovery_ohlcv_1m(n_bars=100)
