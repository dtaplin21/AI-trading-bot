"""Unit tests for level discovery scheduler (no live DB)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chart_watcher.level_discovery_scheduler import (
    LEVEL_DISCOVERY_COOLDOWN_SEC,
    LEVEL_DISCOVERY_INTERVAL_SEC,
    LevelDiscoveryScheduler,
    TriggerPriority,
    _RANGE_CACHE,
)


def test_classify_range_escape():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["MES"] = (100.0, 110.0)
    priority, reason = sched._classify_trigger("MES", 113.0)
    assert priority == TriggerPriority.RANGE_ESCAPE
    assert "range_escape" in reason


def test_classify_regime_shift():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["MES"] = (100.0, 110.0)
    priority, _ = sched._classify_trigger("MES", 120.0)
    assert priority == TriggerPriority.REGIME_SHIFT


def test_classify_interval_when_no_escape():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["MES"] = (100.0, 110.0)
    state = sched._state("MES")
    state.last_interval_run_at = 0.0
    priority, reason = sched._classify_trigger("MES", 105.0)
    assert priority == TriggerPriority.INTERVAL
    assert reason == "scheduled_interval"


def test_coalesce_higher_priority_while_running():
    sched = LevelDiscoveryScheduler()
    state = sched._state("TSLA")
    state.running = True

    async def run():
        return await sched.enqueue(
            "TSLA",
            TriggerPriority.RANGE_ESCAPE,
            "range_escape_test",
            "equity",
        )

    result = asyncio.run(run())
    assert result.status == "coalesced"
    assert state.pending is not None
    assert state.pending.priority == TriggerPriority.RANGE_ESCAPE


def test_interval_skipped_during_cooldown():
    sched = LevelDiscoveryScheduler()
    state = sched._state("TSLA")
    state.last_run_finished_at = time.time()

    async def run():
        return await sched.enqueue(
            "TSLA",
            TriggerPriority.INTERVAL,
            "scheduled_interval",
            "equity",
        )

    result = asyncio.run(run())
    assert result.status == "skipped"
    assert result.reason == "cooldown_active"


@patch("chart_watcher.level_discovery_scheduler.asyncio.create_task")
def test_interval_starts_background_task(mock_create_task):
    sched = LevelDiscoveryScheduler()
    state = sched._state("TSLA")
    state.last_run_finished_at = time.time() - LEVEL_DISCOVERY_COOLDOWN_SEC - 1
    state.last_interval_run_at = time.time() - LEVEL_DISCOVERY_INTERVAL_SEC - 1

    async def run():
        return await sched.enqueue(
            "TSLA",
            TriggerPriority.INTERVAL,
            "scheduled_interval",
            "equity",
        )

    result = asyncio.run(run())
    assert result.status == "started"
    mock_create_task.assert_called_once()
