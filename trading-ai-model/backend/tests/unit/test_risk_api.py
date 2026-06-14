"""Tests for GET/PUT /risk/kill-switch."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import risk.kill_switch_runtime as kill_switch_runtime
from api.main import app


@pytest.fixture(autouse=True)
def reset_runtime(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (None, None))
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)
    kill_switch_runtime.reset_kill_switch_runtime()
    yield
    kill_switch_runtime.reset_kill_switch_runtime()


def test_get_kill_switch_endpoint():
    client = TestClient(app)
    res = client.get("/risk/kill-switch")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False
    assert body["env_default"] is False
    assert body["effective"] is False
    assert body["source"] == "env"
    assert "updated_at" in body


def test_put_kill_switch_endpoint():
    with patch(
        "risk.kill_switch_actions.flatten_all_positions",
        new=AsyncMock(return_value={"live_closed": 0, "paper_closed": 0, "live": [], "paper": []}),
    ):
        client = TestClient(app)
        res = client.put("/risk/kill-switch", json={"enabled": True})
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True
        assert body["effective"] is True

        res2 = client.get("/risk/kill-switch")
        assert res2.json()["effective"] is True

        res3 = client.put("/risk/kill-switch", json={"enabled": False})
        assert res3.json()["enabled"] is False
        assert res3.json()["effective"] is False
