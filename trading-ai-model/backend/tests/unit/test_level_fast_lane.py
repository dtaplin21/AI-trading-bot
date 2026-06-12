"""Tests for actionable watchlist fast lane."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.level_setup import LevelSetup
from pipeline.schemas import OHLCV, RiskDecision, TradeAction
from pipeline.trading_supervisor import TradingPipelineSupervisor


def _level_setup() -> LevelSetup:
    return LevelSetup.from_prices(
        symbol="EURUSD",
        level_price=1.0843,
        entry_side="BUY",
        optimal_tp_pct=0.28,
        optimal_sl_pct=0.12,
        hold_rate=0.72,
        touch_count=20,
        expected_value_pct=0.18,
        role="SUPPORT",
        optimal_rr=2.3,
        exit_win_rate=0.65,
    )


@pytest.mark.asyncio
async def test_fast_lane_skips_full_pipeline(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)
    monkeypatch.setenv("LEVEL_FAST_LANE", "true")

    supervisor = TradingPipelineSupervisor("EURUSD", "5m", news_agent=None, paper_mode=True)
    setup = _level_setup()

    with patch.object(supervisor._level_gate, "check", return_value=setup):
        supervisor._run_methods_concurrent = AsyncMock()  # type: ignore[method-assign]
        supervisor._risk_eng.approve_level_fast_lane = MagicMock(
            return_value=RiskDecision(
                approved=True,
                symbol="EURUSD",
                timestamp=datetime.now(tz=timezone.utc),
                position_size_contracts=1,
            )
        )
        supervisor._execute = AsyncMock(return_value=True)  # type: ignore[method-assign]

        bar = OHLCV(
            symbol="EURUSD",
            timeframe="5m",
            timestamp=datetime.now(tz=timezone.utc),
            open=1.0844,
            high=1.0846,
            low=1.0842,
            close=1.0845,
            volume=100.0,
        )
        result = await supervisor.on_new_bar(bar, ohlcv=None, execute=True)

    assert result.fast_lane is True
    assert result.confluence is None
    assert result.prediction is None
    assert result.plan is not None
    assert result.plan.action == TradeAction.ENTER_LONG
    assert result.executed is True
    supervisor._run_methods_concurrent.assert_not_called()
