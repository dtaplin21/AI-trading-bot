"""Tests for ml.training.retrain_pipeline."""

from datetime import datetime, timezone

import numpy as np
import pytest

from ml.promotion.promotion_policy import PromotionPolicy
from ml.registry.model_registry import ModelRegistry
from ml.training.retrain_pipeline import RetrainPipeline, RetrainResult
from pipeline.world_state_store import WorldStateStore


def _training_row(i: int, label: int = 1) -> dict:
    return {
        "label": label,
        "_timestamp": datetime(2025, 1, 1, 10, i % 60, tzinfo=timezone.utc).isoformat(),
        "feat_a": float(i % 10),
        "feat_b": float(i % 7),
        "signal_rank": 70,
    }


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("MODEL_REGISTRY_FILE", str(tmp_path / "registry.json"))
    monkeypatch.setenv("MODEL_PRODUCTION_PATH", str(tmp_path / "production.pkl"))
    monkeypatch.setenv("MODEL_MIN_SAMPLES", "50")
    monkeypatch.setenv("MODEL_MIN_HOLDOUT_ROWS", "10")
    monkeypatch.setenv("MODEL_AUTO_PROMOTE", "false")

    store = WorldStateStore()
    rows = [_training_row(i, label=i % 2) for i in range(80)]
    monkeypatch.setattr(
        WorldStateStore,
        "get_training_rows",
        lambda self, **kwargs: rows,
    )

    registry = ModelRegistry()
    policy = PromotionPolicy()
    return RetrainPipeline(store, registry, policy), registry


def test_retrain_skips_below_min_samples(pipeline):
    pipe, _ = pipeline
    pipe._world.get_training_rows = lambda **kw: [_training_row(0)]  # type: ignore
    result = pipe.run()
    assert result.skipped is True


def test_retrain_result_to_dict():
    r = RetrainResult()
    r.model_id = "m1"
    r.n_train = 60
    r.n_holdout = 20
    d = r.to_dict()
    assert d["model_id"] == "m1"
    assert d["n_train"] == 60
