"""
tests/unit/test_level_discovery_scheduler.py  (Phase 5)

Tests for the single-flight coalescing scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    is_valid_bar_close,
    normalize_trigger_reason,
    update_range_cache,
    warm_range_cache,
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


def test_normalize_trigger_reason_range_escape():
    reason = normalize_trigger_reason(
        "range_escape_3.2pct_outside_range", TriggerPriority.RANGE_ESCAPE
    )
    assert reason == "range_escape"


def test_normalize_trigger_reason_regime_shift():
    reason = normalize_trigger_reason(
        "regime_shift_9.5pct_outside_range", TriggerPriority.REGIME_SHIFT
    )
    assert reason == "regime_shift"


def test_normalize_trigger_reason_interval():
    reason = normalize_trigger_reason("scheduled_interval", TriggerPriority.INTERVAL)
    assert reason == "interval"


def test_normalize_trigger_reason_startup():
    reason = normalize_trigger_reason("startup", TriggerPriority.RANGE_ESCAPE)
    assert reason == "startup"


def test_update_range_cache_populates_range_cache():
    _RANGE_CACHE.clear()

    def _store(sym: str) -> tuple[float, float]:
        _RANGE_CACHE[sym] = (4861.0, 7321.0)
        return (4861.0, 7321.0)

    with patch(
        "chart_watcher.level_discovery_scheduler._fetch_and_store_range_cache",
        side_effect=_store,
    ) as mock_fetch:
        update_range_cache("MES")

    mock_fetch.assert_called_once_with("MES")
    assert _RANGE_CACHE["MES"] == (4861.0, 7321.0)


def test_classify_trigger_after_warm_cache_mes_stale_envelope():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["MES"] = (4861.0, 7321.0)

    priority, reason = sched._classify_trigger("MES", current_price=7600.0)

    assert priority == TriggerPriority.RANGE_ESCAPE
    assert "range_escape" in reason


@pytest.mark.asyncio
async def test_run_once_passes_trigger_metadata_to_discover_symbol():
    sched = LevelDiscoveryScheduler()
    with patch(
        "chart_watcher.level_discovery_scheduler.asyncio.to_thread",
        new_callable=AsyncMock,
    ) as mock_to_thread:
        mock_to_thread.return_value = MagicMock(
            error=None, skipped_reason=None, coverage_pct=100.0,
            levels_archived=0, levels_reactivated=0, watchlist_active=0, merge_mode="drift",
        )
        with patch(
            "chart_watcher.level_discovery_scheduler.update_range_cache",
        ) as mock_cache:
            await sched._run_once(
                "MES",
                "futures",
                "range_escape_3.2pct_outside_range",
                TriggerPriority.RANGE_ESCAPE,
                runs_coalesced=2,
            )

    mock_cache.assert_called_once_with("MES")
    assert mock_to_thread.await_count == 2
    discover_call = mock_to_thread.await_args_list[0]
    args, kwargs = discover_call
    assert args[0].__name__ == "discover_symbol"
    assert kwargs["trigger_reason"] == "range_escape"
    assert kwargs["runs_coalesced"] == 2
    diag_call = mock_to_thread.await_args_list[1]
    assert diag_call.args[0].__name__ == "log_post_discovery_gate_diagnostics"
    assert diag_call.args[1] == "MES"


@pytest.mark.asyncio
async def test_run_once_skips_gate_diagnostics_when_disabled():
    sched = LevelDiscoveryScheduler()
    with patch(
        "chart_watcher.level_discovery_scheduler.LEVEL_GATE_DIAGNOSTICS_AFTER_DISCOVERY",
        False,
    ):
        with patch(
            "chart_watcher.level_discovery_scheduler.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_to_thread:
            mock_to_thread.return_value = MagicMock(
                error=None, skipped_reason=None, coverage_pct=100.0,
                levels_archived=0, levels_reactivated=0, watchlist_active=0, merge_mode="drift",
            )
            with patch("chart_watcher.level_discovery_scheduler.update_range_cache"):
                await sched._run_once(
                    "MES",
                    "futures",
                    "scheduled_interval",
                    TriggerPriority.INTERVAL,
                    runs_coalesced=0,
                )

    mock_to_thread.assert_awaited_once()
    discover_call = mock_to_thread.await_args_list[0]
    assert discover_call.args[0].__name__ == "discover_symbol"


def test_warm_range_cache_logs_no_active_levels(caplog):
    _RANGE_CACHE.clear()
    with patch(
        "chart_watcher.level_discovery_scheduler._fetch_and_store_range_cache",
        return_value=None,
    ):
        with caplog.at_level(logging.INFO, logger="level_discovery_scheduler"):
            warm_range_cache(["MES"])

    assert "no active levels" in caplog.text


def test_warm_range_cache_logs_envelope(caplog):
    _RANGE_CACHE.clear()
    with patch(
        "chart_watcher.level_discovery_scheduler._fetch_and_store_range_cache",
        return_value=(100.0, 110.0),
    ):
        with caplog.at_level(logging.INFO, logger="level_discovery_scheduler"):
            warm_range_cache(["MES"])

    assert "range cache warmed" in caplog.text


@pytest.mark.asyncio
async def test_startup_discovery_bypasses_cooldown():
    sched = LevelDiscoveryScheduler()
    state = sched._state("MES")
    state.last_run_finished_at = time.time()
    _RANGE_CACHE["MES"] = (4861.0, 7321.0)

    with patch(
        "chart_watcher.level_discovery_scheduler.fetch_last_close",
        return_value=7600.0,
    ):
        with patch(
            "chart_watcher.level_discovery_scheduler.asyncio.create_task",
            side_effect=_consume_create_task,
        ) as mock_task:
            await sched.maybe_enqueue_startup_discovery(["MES"])

    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_startup_discovery_skips_when_price_inside_envelope():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["MES"] = (4861.0, 7321.0)

    with patch(
        "chart_watcher.level_discovery_scheduler.fetch_last_close",
        return_value=7000.0,
    ):
        with patch(
            "chart_watcher.level_discovery_scheduler.asyncio.create_task",
            side_effect=_consume_create_task,
        ) as mock_task:
            await sched.maybe_enqueue_startup_discovery(["MES"])

    mock_task.assert_not_called()


def test_classify_escape_trigger_ignores_zero_close():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["EURUSD"] = (1.05, 1.10)
    priority, reason = sched._classify_escape_trigger("EURUSD", 0.0)
    assert priority is None
    assert reason == ""


@pytest.mark.asyncio
async def test_check_and_maybe_trigger_skips_zero_close():
    sched = LevelDiscoveryScheduler()
    _RANGE_CACHE["EURUSD"] = (1.05, 1.10)
    result = await sched.check_and_maybe_trigger("EURUSD", 0.0, "forex")
    assert result.status == "skipped"
    assert result.reason == "invalid_bar_close"


def test_is_valid_bar_close():
    assert is_valid_bar_close(1.0845)
    assert not is_valid_bar_close(0.0)
    assert not is_valid_bar_close(-1.0)
    assert not is_valid_bar_close(None)
