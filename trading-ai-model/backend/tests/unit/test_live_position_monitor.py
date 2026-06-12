"""Tests for LivePositionMonitor TP/SL and kill switch exits."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live.live_position_monitor import LivePosition, LivePositionMonitor, reset_position_monitor


def _long_position(**kwargs) -> LivePosition:
    defaults = dict(
        trade_id="live-EURUSD-abc",
        symbol="EURUSD",
        side="LONG",
        entry_price=1.0800,
        target_price=1.0850,
        stop_price=1.0750,
        quantity=1000.0,
        broker_order_id="oanda-1",
    )
    defaults.update(kwargs)
    return LivePosition(**defaults)


@pytest.fixture
def monitor():
    reset_position_monitor()
    m = LivePositionMonitor()
    mock_broker = MagicMock()
    mock_broker.close_position = AsyncMock(
        return_value=MagicMock(status="FILLED", broker_order_id="close-1")
    )
    with patch.object(m._router, "get", return_value=mock_broker):
        m._mock_broker = mock_broker
        yield m


@pytest.mark.asyncio
async def test_on_bar_no_close_when_in_range(monitor):
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0820, low=1.0780, close=1.0810)
    assert closed == []
    assert len(monitor.open_positions()) == 1


@pytest.mark.asyncio
async def test_on_bar_tp_hit_long(monitor):
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0860, low=1.0790, close=1.0855)
    assert len(closed) == 1
    assert closed[0].reason == "TP"
    assert closed[0].outcome == "WIN"
    assert closed[0].exit_price == 1.0850
    assert monitor.open_positions() == []
    monitor._mock_broker.close_position.assert_awaited_once()


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
async def test_paper_mode_skips_broker_close(monitor):
    monitor.configure(paper_mode=True)
    monitor.register(_long_position())
    closed = await monitor.on_bar("EURUSD", high=1.0860, low=1.0790, close=1.0855)
    assert len(closed) == 1
    monitor._mock_broker.close_position.assert_not_awaited()
