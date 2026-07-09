"""Tests for live pending touch resolution in LevelIntelligenceSystem."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from ml.features.level_intelligence import (
    LevelIntelligenceSystem,
    _hydrated_pending_symbols,
    _pending_touches,
)


def _ohlcv_df(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=n, freq="1min", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_resolve_pending_classifies_when_window_elapsed(monkeypatch):
    _pending_touches.clear()
    _hydrated_pending_symbols.clear()
    monkeypatch.setattr("ml.features.level_intelligence._db_available", lambda: False)

    system = LevelIntelligenceSystem("ETHUSD", "crypto")
    df = _ohlcv_df(30)
    touched_at = df.index[5].to_pydatetime()
    _pending_touches["ETHUSD"] = [
        {
            "touch_id": 42,
            "touched_at": touched_at,
            "level_price": 100.0,
            "price_at_touch": 100.0,
            "approach": "from_above",
        }
    ]

    with patch.object(system, "update_outcome") as mock_update:
        system._resolve_pending(df, len(df) - 1)

    mock_update.assert_called_once()
    assert _pending_touches["ETHUSD"] == []


def test_process_bar_drains_stale_before_detect(monkeypatch):
    _pending_touches.clear()
    _hydrated_pending_symbols.clear()
    monkeypatch.setattr("ml.features.level_intelligence._db_available", lambda: False)

    system = LevelIntelligenceSystem("ETHUSD", "crypto")
    df = _ohlcv_df(30)

    with patch(
        "ml.features.touch_outcome_classifier.drain_stale_pending_for_symbol",
        return_value=3,
    ) as mock_drain, patch.object(system, "_detect_touch", return_value=None) as mock_detect:
        system.process_bar(df)

    mock_drain.assert_called_once_with(system)
    mock_detect.assert_called_once()
