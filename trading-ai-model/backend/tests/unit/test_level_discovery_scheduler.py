"""
tests/unit/test_level_discovery_scheduler.py  (Phase 5)

Tests for the single-flight coalescing scheduler.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chart_watcher.level_discovery_scheduler import (
    LEVEL_DISCOVERY_COOLDOWN_SEC,
    LEVEL_DISCOVERY_INTERVAL_SEC,
    LevelDiscoveryScheduler,
    PendingRequest,
    TriggerPriority,
    _RANGE_CACHE,
    get_discovery_scheduler,
)


def _consume_create_task(coro):
    """Close coroutines passed to mocked create_task to avoid unawaited warnings."""
    if asyncio.iscoroutine(coro):
        coro.close()
    return None


@pytest.mark.asyncio
async def test_first_trigger_starts_immediately():
    sched = LevelDiscoveryScheduler()
    with patch(
        "chart_watcher.level_discovery_scheduler.asyncio.create_task",
        side_effect=_consume_create_task,
    ) as mock_task:
        result = await sched.enqueue("MES", TriggerPriority.INTERVAL, "scheduled_interval")

    assert result.status == "started"
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_second_trigger_coalesces_while_running():
    sched = LevelDiscoveryScheduler()
    state = sched._state("MES")
    state.running = True

    result = await sched.enqueue("MES", TriggerPriority.RANGE_ESCAPE, "range_escape_test")

    assert result.status == "coalesced"
    assert state.pending is not None
    assert state.pending.priority == TriggerPriority.RANGE_ESCAPE


@pytest.mark.asyncio
async def test_coalesce_keeps_higher_priority():
    sched = LevelDiscoveryScheduler()
    state = sched._state("MES")
    state.running = True
    state.pending = PendingRequest(priority=TriggerPriority.INTERVAL, reason="interval")

    await sched.enqueue("MES", TriggerPriority.RANGE_ESCAPE, "range_escape_higher")
    assert state.pending.priority == TriggerPriority.RANGE_ESCAPE

    state.pending = PendingRequest(priority=TriggerPriority.REGIME_SHIFT, reason="regime")
    await sched.enqueue("MES", TriggerPriority.INTERVAL, "interval_lower")
    assert state.pending.priority == TriggerPriority.REGIME_SHIFT


@pytest.mark.asyncio
async def test_cooldown_blocks_repeated_interval_triggers():
    sched = LevelDiscoveryScheduler()
    state = sched._state("MES")
    state.last_run_finished_at = time.time()

    result = await sched.enqueue("MES", TriggerPriority.INTERVAL, "scheduled_interval")
    assert result.status == "skipped"
    assert result.reason == "cooldown_active"


@pytest.mark.asyncio
async def test_range_escape_bypasses_cooldown():
    sched = LevelDiscoveryScheduler()
    state = sched._state("MES")
    state.last_run_finished_at = time.time()

    with patch(
        "chart_watcher.level_discovery_scheduler.asyncio.create_task",
        side_effect=_consume_create_task,
    ):
        result = await sched.enqueue("MES", TriggerPriority.RANGE_ESCAPE, "range_escape")

    assert result.status == "started"


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


def test_classify_trigger_detects_range_escape():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["ES"] = (4861.0, 7321.0)

    priority, reason = sched._classify_trigger("ES", current_price=7600.0)

    assert priority is not None
    assert priority in (TriggerPriority.RANGE_ESCAPE, TriggerPriority.REGIME_SHIFT)
    assert reason


def test_classify_trigger_no_trigger_when_inside_range():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["TSLA"] = (200.0, 500.0)
    state = sched._state("TSLA")
    state.last_interval_run_at = time.time()

    priority, reason = sched._classify_trigger("TSLA", current_price=350.0)

    assert priority is None
    assert reason == ""


@patch(
    "chart_watcher.level_discovery_scheduler.asyncio.create_task",
    side_effect=_consume_create_task,
)
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


def test_get_discovery_scheduler_returns_singleton():
    s1 = get_discovery_scheduler()
    s2 = get_discovery_scheduler()
    assert s1 is s2
