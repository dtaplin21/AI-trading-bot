"""Unit tests for chart_watcher bar assembly and session schedule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from chart_watcher.bar_assembler import (
    INTRADAY_TIMEFRAMES,
    BarAssembler,
    MultiSymbolAssembler,
    TF_MINUTES,
    _floor_timestamp,
    timeframe_to_seconds,
)
from chart_watcher.session_scheduler import SessionScheduler, WatcherMode
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


def test_floor_timestamp_aligns_5m():
    dt = datetime(2025, 1, 6, 14, 37, tzinfo=timezone.utc)
    assert _floor_timestamp(dt, 5) == datetime(2025, 1, 6, 14, 35, tzinfo=timezone.utc)


def test_intraday_timeframes_under_one_day():
    assert all(TF_MINUTES[tf] < 24 * 60 for tf in INTRADAY_TIMEFRAMES)
    assert "1m" in INTRADAY_TIMEFRAMES
    assert "4h" in INTRADAY_TIMEFRAMES


@pytest.mark.asyncio
async def test_bar_assembler_candle_emits_1m_and_5m():
    completed: list[OHLCV] = []

    async def on_complete(bar: OHLCV) -> None:
        completed.append(bar)

    asm = BarAssembler("MES", ["1m", "5m"], on_complete)

    for i in range(6):
        await asm.on_candle(_bar("MES", i, 100.0 + i))

    tfs = [b.timeframe for b in completed]
    assert tfs.count("1m") == 6
    assert "5m" in tfs
    five = [b for b in completed if b.timeframe == "5m"][0]
    assert five.symbol == "MES"
    assert five.volume == 500.0


@pytest.mark.asyncio
async def test_on_tick_builds_all_intraday_timeframes():
    completed: list[OHLCV] = []

    async def on_complete(bar: OHLCV) -> None:
        completed.append(bar)

    asm = BarAssembler("MES", ["1m", "5m"], on_complete)
    base = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)

    # First tick — no completions (anti-look-ahead)
    out = await asm.on_tick(100.0, volume=1.0, timestamp=base)
    assert out == []

    # Jump into next 1m window — should complete 1m and any higher TF that rolled
    next_ts = base + timedelta(minutes=1)
    out = await asm.on_tick(101.0, volume=1.0, timestamp=next_ts)
    assert len(out) >= 1
    assert any(b.timeframe == "1m" for b in out)

    # After many minutes, multiple intraday TFs should have completed
    for m in range(2, 65):
        await asm.on_tick(100.0 + m, volume=1.0, timestamp=base + timedelta(minutes=m))

    emitted_tfs = {b.timeframe for b in completed}
    assert "1m" in emitted_tfs
    assert "5m" in emitted_tfs
    assert emitted_tfs.issubset(set(INTRADAY_TIMEFRAMES))


@pytest.mark.asyncio
async def test_open_bar_not_emitted_until_superseded():
    asm = BarAssembler("MES", ["5m"], None)
    base = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)
    await asm.on_tick(100.0, timestamp=base)
    peek = asm.current_bar("5m")
    assert peek is not None
    assert peek.close == 100.0

    # Same 5m window — still open
    await asm.on_tick(101.0, timestamp=base + timedelta(minutes=2))
    assert asm.current_bar("5m").close == 101.0

    flushed = await asm.flush()
    flushed_5m = [b for b in flushed if b.timeframe == "5m"]
    assert len(flushed_5m) == 1
    assert asm.current_bar("5m") is None


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
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    assert sched.is_trading("BTC") is True
    assert sched.is_trading("BTCUSD") is True
    assert sched.seconds_until_open("BTC") == 0.0


def test_session_scheduler_replay_always_on():
    sched = SessionScheduler(mode=WatcherMode.REPLAY)
    assert sched.is_trading("MES") is True
    assert sched.watcher_mode == WatcherMode.REPLAY


def test_session_scheduler_cme_saturday_closed():
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    saturday = datetime(2025, 1, 11, 12, 0, tzinfo=timezone.utc).astimezone(
        ZoneInfo("America/New_York")
    )
    assert sched.is_trading("MES", at=saturday) is False


def test_session_scheduler_cme_maintenance_break_closed():
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    wed_break = datetime(2025, 1, 8, 22, 30, tzinfo=timezone.utc)  # 5:30pm ET Wed
    assert sched.is_trading("CL", at=wed_break) is False


def test_session_scheduler_cme_friday_evening_closed():
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    friday_close = datetime(2025, 1, 10, 22, 30, tzinfo=timezone.utc)  # 5:30pm ET
    assert sched.is_trading("MES", at=friday_close) is False


def test_replay_bars_sorted_anti_lookahead():
    bars = [_bar("MES", 2), _bar("NQ", 0), _bar("MES", 1)]
    bars.sort(key=lambda b: b.timestamp)
    assert [b.timestamp for b in bars] == sorted(b.timestamp for b in bars)


def test_parse_bar_timestamp_iso_and_epoch():
    from chart_watcher.chart_watch_runner import _parse_bar_timestamp

    iso = _parse_bar_timestamp("2025-01-06T14:30:00+00:00")
    assert iso.year == 2025 and iso.hour == 14
    epoch = _parse_bar_timestamp(1_705_000_000)
    assert epoch.tzinfo is not None
