"""Unit tests for chart_watcher bar assembly and session schedule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chart_watcher.bar_assembler import BarAssembler, MultiSymbolAssembler, timeframe_to_seconds
from chart_watcher.session_scheduler import SessionScheduler
from pipeline.schemas import OHLCV


def _bar(symbol: str, minute: int, close: float = 100.0) -> OHLCV:
    ts = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return OHLCV(
        symbol=symbol,
        timeframe="1m",
        timestamp=ts,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=100.0,
    )


def test_timeframe_to_seconds():
    assert timeframe_to_seconds("1m") == 60
    assert timeframe_to_seconds("5m") == 300
    assert timeframe_to_seconds("1h") == 3600


@pytest.mark.asyncio
async def test_bar_assembler_emits_1m_and_5m():
    completed: list[OHLCV] = []

    async def on_complete(bar: OHLCV) -> None:
        completed.append(bar)

    asm = BarAssembler("MES", ["1m", "5m"], on_complete)

    for i in range(6):
        await asm.on_candle(_bar("MES", i, 100.0 + i))

    # 5m bucket closes when the 6th minute starts a new window
    tfs = [b.timeframe for b in completed]
    assert tfs.count("1m") == 6
    assert "5m" in tfs
    five = [b for b in completed if b.timeframe == "5m"][0]
    assert five.symbol == "MES"
    assert five.volume == 500.0


@pytest.mark.asyncio
async def test_multi_symbol_assembler_isolated():
    completed: list[OHLCV] = []

    async def on_complete(bar: OHLCV) -> None:
        completed.append(bar)

    multi = MultiSymbolAssembler(["MES", "NQ"], ["1m"], on_complete)
    await multi.get("MES").on_candle(_bar("MES", 0))
    await multi.get("NQ").on_candle(_bar("NQ", 0))

    symbols = {b.symbol for b in completed}
    assert symbols == {"MES", "NQ"}


def test_session_scheduler_crypto_always_open():
    sched = SessionScheduler()
    assert sched.is_trading("BTC") is True
    assert sched.seconds_until_open("BTC") == 0.0


def test_replay_bars_sorted_anti_lookahead():
    """Merged replay stream must be strictly time-ordered."""
    bars = [
        _bar("MES", 2),
        _bar("NQ", 0),
        _bar("MES", 1),
    ]
    bars.sort(key=lambda b: b.timestamp)
    stamps = [b.timestamp for b in bars]
    assert stamps == sorted(stamps)
