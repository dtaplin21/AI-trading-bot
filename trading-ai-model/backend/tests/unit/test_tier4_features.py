"""Tests for Tier 4 ML feature extractors."""

from __future__ import annotations

import pandas as pd

from ml.features.candlestick_features import extract as candlestick_extract
from ml.features.fibonacci_features import extract as fibonacci_extract
from ml.features.markov_features import extract as markov_extract
from ml.features.number_theory_features import extract as number_theory_extract


def _sample_ohlcv(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def test_candlestick_features_populated():
    out = candlestick_extract(_sample_ohlcv(), {})
    assert "cs_body_ratio" in out
    assert "cs_is_bullish" in out


def test_fibonacci_features_populated():
    out = fibonacci_extract(_sample_ohlcv(), {})
    assert "fib_range_position" in out
    assert 0.0 <= out["fib_range_position"] <= 1.0


def test_markov_features_probabilities_sum():
    out = markov_extract(_sample_ohlcv(), {})
    total = out["markov_p_up"] + out["markov_p_down"] + out["markov_p_flat"]
    assert 0.99 <= total <= 1.01


def test_number_theory_features_populated():
    out = number_theory_extract(_sample_ohlcv(), {})
    assert "nt_369_dist_pct" in out
    assert "nt_phi_nearest_dist" in out
