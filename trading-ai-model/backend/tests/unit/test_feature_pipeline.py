"""Tests for FeaturePipeline shared indicator cache."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from data.storage.feature_store import FeatureStore, get_feature_store
from ml.features.feature_pipeline import FeaturePipeline


def _sample_ohlcv(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000 + close,
        },
        index=idx,
    )


def test_build_computes_shared_indicators():
    get_feature_store().clear()
    pipeline = FeaturePipeline()
    ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    features = pipeline.build(
        {
            "symbol": "TSLA",
            "timeframe": "5m",
            "timestamp": ts,
            "ohlcv": _sample_ohlcv(),
        }
    )
    assert "rsi_14" in features
    assert "macd" in features
    assert "atr_14" in features
    assert features["close"] == 139.0


def test_compute_returns_training_feature_keys():
    pipeline = FeaturePipeline()
    features = pipeline.compute(_sample_ohlcv())
    assert "rsi_14" in features
    assert "macd_line" in features
    assert "atr_14" in features
    assert "volume_ratio" in features


def test_build_uses_cache_on_second_call():
    get_feature_store().clear()
    pipeline = FeaturePipeline()
    ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    payload = {
        "symbol": "TSLA",
        "timeframe": "5m",
        "timestamp": ts,
        "ohlcv": _sample_ohlcv(),
    }
    first = pipeline.build(payload)
    second = pipeline.build(payload)
    assert second == first
    assert get_feature_store().get_features("TSLA", "5m", ts) == first
