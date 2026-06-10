"""Tests for ReversalPredictor."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.models.reversal_predictor import (
    ReversalPredictor,
    build_prediction_features,
    get_predictor,
    predict_reversal,
    reload_all,
)


class _MockModel:
    def predict(self, X):
        return np.array([0.73])


@pytest.fixture
def model_dir(tmp_path):
    sym_dir = tmp_path / "EURUSD"
    sym_dir.mkdir(parents=True)
    model_path = sym_dir / "reversal_model_test.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(_MockModel(), f)

    levels_path = sym_dir / "levels_test.json"
    levels_path.write_text(
        json.dumps(
            {
                "symbol": "EURUSD",
                "asset_class": "forex",
                "level_count": 0,
                "levels": [],
            }
        )
    )

    meta = {
        "model_path": str(model_path),
        "levels_path": str(levels_path),
        "asset_class": "forex",
        "feature_cols": ["rsi_14", "level_nearest_dist_pct"],
        "metrics": {"auc": 0.71, "base_rate": 0.32},
    }
    (sym_dir / "latest.json").write_text(json.dumps(meta))
    return tmp_path


def test_predictor_loads_and_predicts(model_dir):
    reload_all()
    pred = ReversalPredictor("EURUSD", model_dir=str(model_dir)).load()
    assert pred.is_loaded
    assert pred.predict({"rsi_14": 55.0, "level_nearest_dist_pct": 0.5}) == 0.73


def test_fallback_when_model_missing(tmp_path):
    reload_all()
    pred = ReversalPredictor("TSLA", model_dir=str(tmp_path)).load()
    assert not pred.is_loaded
    assert pred.predict({}) == 0.30


def test_registry_caches_predictor(model_dir):
    reload_all()
    a = get_predictor("EURUSD", model_dir=str(model_dir))
    b = get_predictor("EURUSD", model_dir=str(model_dir))
    assert a is b


def test_build_prediction_features_merges_shared():
    ohlcv = pd.DataFrame(
        {
            "open": np.linspace(100, 120, 30),
            "high": np.linspace(101, 121, 30),
            "low": np.linspace(99, 119, 30),
            "close": np.linspace(100, 120, 30),
            "volume": np.full(30, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC"),
    )
    out = build_prediction_features(
        "EURUSD",
        ohlcv=ohlcv,
        shared_features={"cs_doji": 1},
        fused={"signal_rank": 80},
    )
    assert out["cs_doji"] == 1
    assert out["signal_rank"] == 80
    assert "rsi_14" in out
