"""Tests for LiveExecutionAgent safeguards and sizing."""

from __future__ import annotations

import os

import pytest

from live.live_execution_agent import LiveExecutionAgent, reset_live_execution_agent
from live.live_position_monitor import reset_position_monitor
from pipeline.level_setup import LevelSetup


def _setup(
    symbol: str = "EURUSD",
    level_price: float = 1.0843,
    entry_side: str = "BUY",
    optimal_tp_pct: float = 0.28,
    optimal_sl_pct: float = 0.12,
    hold_rate: float = 0.70,
    touch_count: int = 20,
    expected_value_pct: float = 0.18,
) -> LevelSetup:
    return LevelSetup.from_prices(
        symbol=symbol,
        level_price=level_price,
        entry_side=entry_side,
        optimal_tp_pct=optimal_tp_pct,
        optimal_sl_pct=optimal_sl_pct,
        hold_rate=hold_rate,
        touch_count=touch_count,
        expected_value_pct=expected_value_pct,
    )


def test_size_position_forex_micro_lot():
    agent = LiveExecutionAgent()
    setup = _setup()
    qty = agent._size_position(setup, 10_000.0)
    assert qty >= 1000.0
    assert qty % 1000 == 0


def test_size_position_futures_min_one():
    agent = LiveExecutionAgent()
    setup = _setup(symbol="MES", level_price=5500.0, entry_side="SELL")
    qty = agent._size_position(setup, 10_000.0)
    assert qty >= 1.0


def test_size_position_zero_when_no_sl_distance():
    agent = LiveExecutionAgent()
    setup = _setup()
    setup.stop_price = setup.entry_price
    assert agent._size_position(setup, 10_000.0) == 0.0


@pytest.mark.asyncio
async def test_execute_level_kill_switch(monkeypatch):
    import risk.kill_switch_runtime as kill_switch_runtime

    reset_live_execution_agent()
    reset_position_monitor()
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    kill_switch_runtime.reset_kill_switch_runtime()
    agent = LiveExecutionAgent()
    ok = await agent.execute_level(_setup())
    assert ok is False


@pytest.mark.asyncio
async def test_execute_level_blocked_in_paper_mode(monkeypatch):
    reset_live_execution_agent()
    reset_position_monitor()
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    agent = LiveExecutionAgent()
    ok = await agent.execute_level(_setup())
    assert ok is False
