"""Tests for touch outcome classification."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml.features.touch_outcome_classifier import (
    bar_index_for_timestamp,
    classify_from_forward_bars,
    compute_outcome,
)


def test_compute_outcome_from_above_hold():
    result = compute_outcome(100.0, "from_above", future_high=100.20, future_low=99.90)
    assert result.outcome == "hold"
    assert result.price_move_after > 0


def test_compute_outcome_from_above_break():
    result = compute_outcome(100.0, "from_above", future_high=100.05, future_low=99.70)
    assert result.outcome == "break"


def test_compute_outcome_from_below_hold():
    result = compute_outcome(100.0, "from_below", future_high=100.10, future_low=99.80)
    assert result.outcome == "hold"


def test_classify_from_forward_bars_returns_none_without_data():
    assert classify_from_forward_bars(100.0, "from_above", [], []) is None


def test_bar_index_for_timestamp_finds_nearest_bar():
    idx = pd.date_range("2024-01-01", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=idx)
    target = datetime(2024, 1, 1, 0, 2, 30, tzinfo=timezone.utc)
    assert bar_index_for_timestamp(df, target) == 2
