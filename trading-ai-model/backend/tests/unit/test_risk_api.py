"""Tests for GET/PUT /risk/kill-switch."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import risk.kill_switch_runtime as kill_switch_runtime
import risk.order_sizing_runtime as order_sizing_runtime
from api.main import app


@pytest.fixture(autouse=True)
def reset_runtime(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (None, None))
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)
    kill_switch_runtime.reset_kill_switch_runtime()
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "5")
    monkeypatch.setattr(order_sizing_runtime, "_database_url", lambda: None)
    order_sizing_runtime.reset_order_sizing_runtime()
    yield
    kill_switch_runtime.reset_kill_switch_runtime()
    order_sizing_runtime.reset_order_sizing_runtime()


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


def test_get_order_sizing_endpoint():
    client = TestClient(app)
    res = client.get("/risk/order-sizing")
    assert res.status_code == 200
    body = res.json()
    assert body["coinbase_order_usd"] == 5.0
    assert body["oanda_order_usd"] == 5.0
    assert body["source"] == "env"
    assert "limits" in body


def test_put_order_sizing_endpoint(monkeypatch):
    monkeypatch.setattr(order_sizing_runtime, "_write_postgres", lambda cb, oa: None)
    client = TestClient(app)
    res = client.put("/risk/order-sizing", json={"coinbase_order_usd": 12, "oanda_order_usd": 8})
    assert res.status_code == 200
    body = res.json()
    assert body["coinbase_order_usd"] == 12.0
    assert body["oanda_order_usd"] == 8.0
    assert body["source"] == "memory"

    res2 = client.get("/risk/order-sizing")
    assert res2.json()["coinbase_order_usd"] == 12.0
