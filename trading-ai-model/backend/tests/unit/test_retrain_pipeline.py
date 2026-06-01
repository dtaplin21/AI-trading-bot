"""Tests for model registry and retrain pipeline."""

import pickle
from pathlib import Path

import pytest

from agents.learning.model_registry import ModelRegistry, ModelStage
from agents.learning.retrain_pipeline import RetrainPipeline


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("MODEL_REGISTRY_FILE", str(tmp_path / "registry.json"))
    monkeypatch.setenv("MODEL_PRODUCTION_PATH", str(tmp_path / "production.pkl"))
    return ModelRegistry()


def test_register_and_list(registry):
    record = registry.register_candidate(
        model_obj={"stub": True},
        n_samples=250,
        holdout_brier=0.2,
        production_brier_at_train=0.23,
        holdout_auc=0.6,
        positive_rate=0.48,
        brier_improvement=0.03,
    )
    assert record.status == ModelStage.CANDIDATE.value
    assert len(registry.list_models()) == 1


def test_promotion_to_production(registry, tmp_path):
    pkl = tmp_path / "candidate.pkl"
    with open(pkl, "wb") as f:
        pickle.dump({"model": "stub"}, f)

    record = registry.register_candidate(
        model_obj={"model": "stub"},
        n_samples=250,
        holdout_brier=0.2,
        production_brier_at_train=0.23,
        holdout_auc=0.6,
        positive_rate=0.48,
        brier_improvement=0.03,
        model_id="m1",
    )
    registry.set_status("m1", ModelStage.APPROVED.value)
    ok = registry.promote_to_production("m1", "admin")
    assert ok is True
    assert registry.get_model("m1").status == ModelStage.PRODUCTION.value
    assert registry.production_path.exists()


def test_rollback(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PRODUCTION_PATH", str(tmp_path / "production.pkl"))
    r1 = registry.register_candidate(
        model_obj={"v": 1},
        n_samples=200,
        holdout_brier=0.25,
        production_brier_at_train=0.30,
        holdout_auc=0.56,
        positive_rate=0.5,
        brier_improvement=0.05,
        model_id="old_prod",
    )
    registry.promote_to_production("old_prod", "test")
    r2 = registry.register_candidate(
        model_obj={"v": 2},
        n_samples=220,
        holdout_brier=0.20,
        production_brier_at_train=0.25,
        holdout_auc=0.58,
        positive_rate=0.5,
        brier_improvement=0.05,
        model_id="new_prod",
    )
    registry.promote_to_production("new_prod", "test")
    assert registry.get_model("old_prod").status == ModelStage.SUPERSEDED.value

    ok = registry.rollback("old_prod", "admin")
    assert ok is True
    assert registry.get_model("old_prod").status == ModelStage.PRODUCTION.value


def test_retrain_not_due_without_history(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    from config.settings import get_settings

    get_settings.cache_clear()
    pipeline = RetrainPipeline()
    assert pipeline.due_for_retrain() is True
