"""Tests for pipeline TradingPipelineSupervisor."""

import asyncio
from datetime import datetime, timezone

import pytest

from pipeline.schemas import OHLCV
from pipeline.trading_supervisor import TradingPipelineSupervisor
from tests.fixtures.sample_ohlcv import sample_ohlcv


@pytest.fixture
def pipeline_supervisor(monkeypatch):
    monkeypatch.setenv("LEVEL_GATE_DISABLED", "true")
    return TradingPipelineSupervisor("MES", "5m", news_agent=None, paper_mode=True)


@pytest.mark.asyncio
async def test_on_new_bar_runs_full_pipeline(pipeline_supervisor):
    ohlcv = sample_ohlcv(60)
    last = ohlcv.iloc[-1]
    bar = OHLCV(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        open=float(last["open"]),
        high=float(last["high"]),
        low=float(last["low"]),
        close=float(last["close"]),
        volume=float(last.get("volume", 0)),
    )
    result = await pipeline_supervisor.on_new_bar(
        bar,
        ohlcv=ohlcv,
        historical_sample_size=500,
        execute=False,
    )
    assert not result.errors
    assert result.fused is not None
    assert 0 <= result.fused.signal_rank <= 100
    assert result.audit is not None


def test_compute_signal_rank_penalizes_news_block(pipeline_supervisor):
    from datetime import datetime, timezone

    from pipeline.schemas import FusedFeatureSet

    fused = FusedFeatureSet(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        signal_rank=80,
        wick_rejection_score=0.8,
        news_trading_blocked=True,
    )
    rank = pipeline_supervisor.compute_signal_rank(fused)
    assert rank < 80
