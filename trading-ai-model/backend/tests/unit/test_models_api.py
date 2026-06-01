"""Tests for api/routes/models.py."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import models as models_routes
from learning.learning_agent import LearningAgent
from ml.promotion.promotion_policy import PromotionPolicy
from ml.registry.model_registry import ModelRegistry
from pipeline.world_state_store import WorldStateStore
from risk.risk_engine import RiskEngine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("MODEL_REGISTRY_FILE", str(tmp_path / "registry.json"))
    monkeypatch.setenv("MODEL_PRODUCTION_PATH", str(tmp_path / "production.pkl"))

    store = WorldStateStore()
    agent = LearningAgent(store, RiskEngine(), on_model_reload=lambda _: None)
    models_routes.set_agents(agent, agent._registry, agent._policy)

    app = FastAPI()
    app.include_router(models_routes.router)
    return TestClient(app)


def test_get_policy(client):
    resp = client.get("/models/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert "auto_promote" in data
    assert "min_samples" in data


def test_list_models_empty(client):
    resp = client.get("/models")
    assert resp.status_code == 200
    assert resp.json() == []


def test_approve_rejects_auto(client):
    resp = client.post(
        "/models/fake_id/approve",
        json={"approved_by": "auto"},
    )
    assert resp.status_code == 400
