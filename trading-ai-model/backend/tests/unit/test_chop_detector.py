"""Tests for ChopDetector."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.models.chop_detector import ChopDetector, detect_chop


def _trending_df(n: int = 80) -> pd.DataFrame:
    close = np.linspace(100, 150, n)
    return pd.DataFrame(
        {
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
    )


def _choppy_df(n: int = 80) -> pd.DataFrame:
    close = 100 + np.sin(np.linspace(0, 12 * np.pi, n)) * 0.5
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
    )


def test_trending_scores_lower_than_choppy():
    detector = ChopDetector()
    trend = detector.score(_trending_df())
    chop = detector.score(_choppy_df())
    assert 0.0 <= trend <= 1.0
    assert 0.0 <= chop <= 1.0
    assert trend < chop


def test_classify_labels():
    detector = ChopDetector()
    assert detector.classify(_trending_df()) in ("trending", "neutral", "choppy")
    assert detector.classify(_choppy_df()) in ("trending", "neutral", "choppy")


def test_detect_chop_module_helper():
    score = detect_chop(_trending_df())
    assert 0.0 <= score <= 1.0
