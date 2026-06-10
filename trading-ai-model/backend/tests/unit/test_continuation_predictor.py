"""Tests for ContinuationPredictor."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.models.continuation_predictor import ContinuationPredictor, predict_continuation


def _trending_df(n: int = 80) -> pd.DataFrame:
    close = np.linspace(100, 150, n)
    return pd.DataFrame(
        {
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.linspace(1000, 3000, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
    )


def _flat_df(n: int = 80) -> pd.DataFrame:
    close = np.full(n, 100.0) + np.random.default_rng(0).normal(0, 0.05, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
    )


def test_trending_scores_higher_than_flat():
    detector = ContinuationPredictor()
    trend = detector.score(_trending_df())
    flat = detector.score(_flat_df())
    assert 0.0 <= trend <= 1.0
    assert 0.0 <= flat <= 1.0
    assert trend > flat


def test_score_with_direction_on_uptrend():
    detector = ContinuationPredictor()
    score, direction = detector.score_with_direction(_trending_df())
    assert score >= 0.4
    assert direction == "up"


def test_predict_continuation_module_helper():
    score = predict_continuation(_trending_df())
    assert 0.0 <= score <= 1.0
