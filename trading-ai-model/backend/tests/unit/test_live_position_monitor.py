"""Tests for LivePositionMonitor TP/SL and kill switch exits."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live.live_position_monitor import LivePosition, LivePositionMonitor, reset_position_monitor


def _long_position(**kwargs: Any) -> LivePosition:
    base = LivePosition(
        trade_id="live-EURUSD-abc",
        symbol="EURUSD",
        side="LONG",
        entry_price=1.0800,
        target_price=1.0850,
        stop_price=1.0750,
        quantity=1000.0,
        broker_order_id="oanda-1",
    )
    return replace(base, **kwargs) if kwargs else base


@dataclass
class _MonitorTestContext:
    monitor: LivePositionMonitor
    mock_broker: MagicMock


@pytest.fixture
def monitor_context() -> Iterator[_MonitorTestContext]:
    reset_position_monitor()
    m = LivePositionMonitor()
    mock_broker = MagicMock()
    mock_broker.close_position = AsyncMock(
        return_value=MagicMock(status="FILLED", broker_order_id="close-1")
    )
    with patch.object(m._router, "get", return_value=mock_broker):
        yield _MonitorTestContext(monitor=m, mock_broker=mock_broker)


@pytest.fixture
def monitor(monitor_context: _MonitorTestContext) -> LivePositionMonitor:
    return monitor_context.monitor


@pytest.mark.asyncio
async def test_on_bar_no_close_when_in_range(monitor):
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0820, low=1.0780, close=1.0810)
    assert closed == []
    assert len(monitor.open_positions()) == 1


@pytest.mark.asyncio
async def test_on_bar_tp_hit_long(monitor, monitor_context):
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0860, low=1.0790, close=1.0855)
    assert len(closed) == 1
    assert closed[0].reason == "TP"
    assert closed[0].outcome == "WIN"
    assert closed[0].exit_price == 1.0850
    assert monitor.open_positions() == []
    monitor_context.mock_broker.close_position.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_bar_sl_hit_long(monitor):
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0790, low=1.0740, close=1.0745)
    assert len(closed) == 1
    assert closed[0].reason == "SL"
    assert closed[0].outcome == "LOSS"


@pytest.mark.asyncio
async def test_on_bar_kill_switch(monkeypatch, monitor):
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0810, low=1.0790, close=1.0805)
    assert len(closed) == 1
    assert closed[0].reason == "KILL_SWITCH"


@pytest.mark.asyncio
async def test_paper_mode_skips_broker_close(monitor, monitor_context):
    monitor.configure(paper_mode=True)
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0860, low=1.0790, close=1.0855)
    assert len(closed) == 1
    monitor_context.mock_broker.close_position.assert_not_awaited()
