"""Tests for ml/registry/model_registry.py."""

import json
from pathlib import Path

import pytest

from ml.registry.model_registry import ModelRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("MODEL_REGISTRY_FILE", str(tmp_path / "registry.json"))
    monkeypatch.setenv("MODEL_PRODUCTION_PATH", str(tmp_path / "production.pkl"))
    return ModelRegistry()


def test_status_summary(registry):
    registry.register_candidate(
        model_obj={},
        n_samples=200,
        holdout_brier=0.22,
        production_brier_at_train=0.25,
        holdout_auc=0.57,
        positive_rate=0.5,
        brier_improvement=0.03,
    )
    summary = registry.status_summary()
    assert summary["total_models"] == 1
    assert summary["by_status"]["candidate"] == 1


def test_registry_persists(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_REGISTRY_FILE", str(tmp_path / "registry.json"))
    registry.register_candidate(
        model_obj={},
        n_samples=200,
        holdout_brier=0.22,
        production_brier_at_train=0.25,
        holdout_auc=0.57,
        positive_rate=0.5,
        brier_improvement=0.03,
        model_id="persist_me",
    )
    reloaded = ModelRegistry()
    assert reloaded.get_model("persist_me") is not None
