"""Tests for runtime news polling switch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agents.news_runtime as news_runtime
from api.main import app


@pytest.fixture(autouse=True)
def reset_polling_override():
    news_runtime._polling_override = None
    yield
    news_runtime._polling_override = None


def test_polling_status_endpoint():
    client = TestClient(app)
    res = client.get("/news/polling")
    assert res.status_code == 200
    body = res.json()
    assert "enabled" in body
    assert "running" in body


@pytest.mark.asyncio
async def test_set_polling_disabled_stops_agent(monkeypatch):
    agent = news_runtime.get_news_agent()
    stopped = False

    async def fake_stop():
        nonlocal stopped
        stopped = True
        agent._running = False

    monkeypatch.setattr(agent, "stop", fake_stop)
    agent._running = True

    status = await news_runtime.set_news_polling_enabled(False)
    assert status["enabled"] is False
    assert stopped is True


@pytest.mark.asyncio
async def test_set_polling_enabled_starts_background(monkeypatch):
    agent = news_runtime.get_news_agent()
    started = False

    def fake_start():
        nonlocal started
        started = True
        agent._running = True

    monkeypatch.setattr(agent, "start_background", fake_start)
    agent._running = False
    agent._task = None

    status = await news_runtime.set_news_polling_enabled(True)
    assert status["enabled"] is True
    assert started is True


def test_put_polling_endpoint():
    client = TestClient(app)
    res = client.put("/news/polling", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["enabled"] is False
