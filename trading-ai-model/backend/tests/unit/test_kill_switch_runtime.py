"""Tests for cross-process kill switch runtime."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

import risk.kill_switch_runtime as kill_switch_runtime


@pytest.fixture(autouse=True)
def reset_runtime():
    kill_switch_runtime.reset_kill_switch_runtime()
    yield
    kill_switch_runtime.reset_kill_switch_runtime()


def test_defaults_to_env(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    assert kill_switch_runtime.is_kill_switch_active() is False
    status = kill_switch_runtime.get_kill_switch_status()
    assert status["enabled"] is False
    assert status["source"] == "env"
    assert status["env_default"] is False
    assert status["effective"] is False


def test_env_true(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    assert kill_switch_runtime.is_kill_switch_active() is True
    assert kill_switch_runtime.get_kill_switch_status()["source"] == "env"


@pytest.mark.asyncio
async def test_set_enabled_updates_memory_and_env(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)

    status = await kill_switch_runtime.set_kill_switch_enabled(True)

    assert kill_switch_runtime.is_kill_switch_active() is True
    assert status["enabled"] is True
    assert status["source"] == "memory"
    assert status["env_default"] is False


@pytest.mark.asyncio
async def test_set_disabled_clears_active(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)

    status = await kill_switch_runtime.set_kill_switch_enabled(False)

    assert kill_switch_runtime.is_kill_switch_active() is False
    assert status["enabled"] is False
    assert status["source"] == "memory"


def test_memory_override_beats_env(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    kill_switch_runtime._memory_override = False
    assert kill_switch_runtime.is_kill_switch_active() is False
    assert kill_switch_runtime.get_kill_switch_status()["source"] == "memory"


def test_postgres_read_when_no_memory_override(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (True, None))

    assert kill_switch_runtime.is_kill_switch_active() is True
    status = kill_switch_runtime.get_kill_switch_status()
    assert status["enabled"] is True
    assert status["effective"] is True
    assert status["source"] == "postgres"


def test_db_override_beats_env_default(monkeypatch):
    """Postgres enabled=true overrides startup RISK_KILL_SWITCH=false."""
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (True, None))

    assert kill_switch_runtime.is_kill_switch_active() is True
    status = kill_switch_runtime.get_kill_switch_status()
    assert status["env_default"] is False
    assert status["enabled"] is True


def test_get_status_includes_effective_and_source(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    status = kill_switch_runtime.get_kill_switch_status()
    assert set(status.keys()) >= {"enabled", "env_default", "updated_at", "effective", "source"}


@pytest.mark.asyncio
async def test_set_persists_via_write_postgres(monkeypatch):
    writes: list[bool] = []
    monkeypatch.setattr(
        kill_switch_runtime,
        "_write_postgres",
        lambda enabled: writes.append(enabled) or None,
    )
    with patch(
        "risk.kill_switch_actions.flatten_all_positions",
        new=AsyncMock(return_value={"live_closed": 0, "paper_closed": 0, "live": [], "paper": []}),
    ):
        await kill_switch_runtime.set_kill_switch_enabled(True)

    assert writes == [True]
    assert os.getenv("RISK_KILL_SWITCH") == "true"


def test_postgres_cache_reuses_read_within_ttl(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    calls = {"n": 0}

    def counting_read():
        calls["n"] += 1
        return True, None

    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", counting_read)
    monkeypatch.setattr(kill_switch_runtime, "_POSTGRES_CACHE_TTL_SEC", 2.0)

    assert kill_switch_runtime.is_kill_switch_active() is True
    assert kill_switch_runtime.is_kill_switch_active() is True
    assert calls["n"] == 1


def test_postgres_cache_refreshes_after_ttl(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    calls = {"n": 0}

    def counting_read():
        calls["n"] += 1
        return calls["n"] == 1, None

    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", counting_read)
    monkeypatch.setattr(kill_switch_runtime, "_POSTGRES_CACHE_TTL_SEC", 0.0)

    assert kill_switch_runtime.is_kill_switch_active() is True
    assert kill_switch_runtime.is_kill_switch_active() is False
    assert calls["n"] == 2
