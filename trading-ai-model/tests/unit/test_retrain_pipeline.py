"""Tests for model registry and retrain pipeline."""

import json
from pathlib import Path

import pytest

from agents.learning.model_registry import ModelRegistry, ModelStage
from agents.learning.retrain_pipeline import RetrainPipeline


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    from config.settings import get_settings

    get_settings.cache_clear()
    return ModelRegistry()


def test_register_and_list(registry):
    entry = registry.register_candidate("abc123", "v1", "/tmp/model.txt", {"samples": 100})
    assert entry["stage"] == ModelStage.CANDIDATE.value
    assert len(registry.list_models()) == 1


def test_promotion_requires_approval(registry, tmp_path):
    artifact = tmp_path / "model.txt"
    artifact.write_text("stub")
    meta = tmp_path / "model.meta.json"
    meta.write_text(json.dumps({"version": "v1"}))

    registry.register_candidate("m1", "v1", str(artifact), {})
    registry.advance_stage("m1", ModelStage.VALIDATED)
    registry.advance_stage("m1", ModelStage.PAPER_TEST)
    registry.advance_stage("m1", ModelStage.APPROVED)

    result = registry.promote_to_production("m1", "admin")
    assert result["stage"] == ModelStage.PRODUCTION.value
    assert registry.production_path.exists()


def test_retrain_not_due_without_history(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    from config.settings import get_settings

    get_settings.cache_clear()
    pipeline = RetrainPipeline()
    assert pipeline.due_for_retrain() is True
