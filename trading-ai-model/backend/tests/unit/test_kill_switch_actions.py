"""Tests for instant kill-switch flatten (Phase 4b)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import risk.kill_switch_actions as kill_switch_actions
import risk.kill_switch_runtime as kill_switch_runtime
from live.live_position_monitor import LivePosition, LivePositionMonitor, reset_position_monitor
from paper_trading.paper_trader import PaperTrader, reset_paper_trader
from paper_trading.position_book import reset_position_book


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (None, None))
    kill_switch_runtime.reset_kill_switch_runtime()
    kill_switch_actions.reset_kill_flatten_arm()
    reset_position_monitor()
    reset_position_book()
    reset_paper_trader()
    yield
    kill_switch_runtime.reset_kill_switch_runtime()
    kill_switch_actions.reset_kill_flatten_arm()


@pytest.mark.asyncio
async def test_flatten_all_closes_live_positions():
    monitor = LivePositionMonitor()
    monitor.register(
        LivePosition(
            trade_id="live-MES-1",
            symbol="MES",
            side="LONG",
            entry_price=5500.0,
            target_price=5520.0,
            stop_price=5490.0,
            quantity=1.0,
            broker_order_id="ord-1",
        )
    )
    mock_broker = MagicMock()
    mock_broker.close_position = AsyncMock(return_value=MagicMock(status="FILLED"))

    with patch.object(monitor._router, "get", return_value=mock_broker), patch.object(
        kill_switch_actions, "latest_close_price", return_value=5505.0
    ):
        closed = await monitor.flatten_all()

    assert len(closed) == 1
    assert closed[0].reason == "KILL_SWITCH"
    assert monitor.open_positions() == []
    mock_broker.close_position.assert_awaited_once()


def test_close_all_at_market_closes_paper_positions():
    book = reset_position_book()
    pos = book.open_position(
        symbol="MES",
        direction="long",
        entry_price=5500.0,
        stop_loss=5490.0,
        take_profit=5520.0,
        quantity=1,
    )
    pos.current_price = 5508.0

    trader = PaperTrader(learning_agent=MagicMock())
    with patch.object(trader, "_finalize_close", wraps=trader._finalize_close) as finalize:
        results = trader.close_all_at_market(reason="kill_switch")

    assert len(results) == 1
    assert results[0]["status"] == "closed"
    assert results[0]["exit_reason"] == "kill_switch"
    assert book.count() == 0
    finalize.assert_called_once()


@pytest.mark.asyncio
async def test_set_kill_switch_enable_flattens(monkeypatch):
    book = reset_position_book()
    book.open_position(
        symbol="NQ",
        direction="short",
        entry_price=19000.0,
        stop_loss=19050.0,
        take_profit=18900.0,
        quantity=1,
    )
    trader = PaperTrader(learning_agent=MagicMock())
    reset_paper_trader()
    with patch("paper_trading.paper_trader.get_paper_trader", return_value=trader), patch.object(
        kill_switch_actions,
        "flatten_all_positions",
        new=AsyncMock(return_value={"live_closed": 0, "paper_closed": 1, "live": [], "paper": []}),
    ) as flatten:
        status = await kill_switch_runtime.set_kill_switch_enabled(True)

    flatten.assert_awaited_once()
    assert status["enabled"] is True
    assert status["flatten"]["paper_closed"] == 1


@pytest.mark.asyncio
async def test_set_kill_switch_disable_does_not_flatten():
    kill_switch_runtime._memory_override = True
    with patch(
        "risk.kill_switch_actions.flatten_all_positions",
        new=AsyncMock(),
    ) as flatten:
        await kill_switch_runtime.set_kill_switch_enabled(False)

    flatten.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_flatten_runs_once_per_activation(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    kill_switch_runtime.reset_kill_switch_runtime()

    with patch(
        "risk.kill_switch_actions.flatten_all_positions",
        new=AsyncMock(return_value={"live_closed": 0, "paper_closed": 0, "live": [], "paper": []}),
    ) as flatten:
        first = await kill_switch_actions.maybe_flatten_on_kill_active()
        second = await kill_switch_actions.maybe_flatten_on_kill_active()

    assert first is not None
    assert second is None
    flatten.assert_awaited_once()
