"""Tests for incremental tick aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from data.processors.tick_aggregator import (
    MultiSymbolAggregator,
    TickAggregator,
    bar_dict_to_ohlcv,
    ticks_to_bars,
)


def test_single_bar_updates_in_place():
    agg = TickAggregator("EURUSD", "1m")
    t0 = datetime(2024, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
    assert agg.update(1.08, 1.0, t0) is None
    assert agg.update(1.09, 2.0, t0 + timedelta(seconds=10)) is None
    current = agg.get_current()
    assert current is not None
    assert current["open"] == 1.08
    assert current["high"] == 1.09
    assert current["low"] == 1.08
    assert current["close"] == 1.09
    assert current["volume"] == 3.0


def test_bar_closes_on_new_period():
    agg = TickAggregator("EURUSD", "1m")
    t0 = datetime(2024, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
    t1 = datetime(2024, 1, 1, 12, 1, 5, tzinfo=timezone.utc)

    agg.update(1.08, 1.0, t0)
    completed = agg.update(1.10, 1.0, t1)

    assert completed is not None
    assert completed["open"] == 1.08
    assert completed["close"] == 1.08
    current = agg.get_current()
    assert current is not None
    assert current["open"] == 1.10


def test_bar_open_time_snaps_to_boundary():
    agg = TickAggregator("EURUSD", "5m")
    ts = datetime(2024, 1, 1, 12, 7, 45, tzinfo=timezone.utc)
    agg.update(100.0, 1.0, ts)
    current = agg.get_current()
    assert current is not None
    assert current["time"] == datetime(2024, 1, 1, 12, 5, 0, tzinfo=timezone.utc)


def test_multi_symbol_aggregator_multiple_timeframes():
    agg = MultiSymbolAggregator(timeframes=["1m", "5m"])
    t0 = datetime(2024, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
    t1 = datetime(2024, 1, 1, 12, 1, 10, tzinfo=timezone.utc)

    assert agg.update("TSLA", 250.0, 10.0, t0) == []
    completed = agg.update("TSLA", 251.0, 5.0, t1)
    assert len(completed) == 1
    assert completed[0]["timeframe"] == "1m"
    assert completed[0]["symbol"] == "TSLA"


def test_bar_dict_to_ohlcv():
    bar = {
        "symbol": "TSLA",
        "timeframe": "1m",
        "time": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100.0,
    }
    ohlcv = bar_dict_to_ohlcv(bar)
    assert ohlcv.symbol == "TSLA"
    assert ohlcv.close == 1.5


def test_ticks_to_bars_batch():
    ticks = [
        {
            "symbol": "EURUSD",
            "price": 1.0,
            "size": 1.0,
            "timestamp": datetime(2024, 1, 1, 12, 0, 10, tzinfo=timezone.utc),
        },
        {
            "symbol": "EURUSD",
            "price": 1.1,
            "size": 1.0,
            "timestamp": datetime(2024, 1, 1, 12, 1, 10, tzinfo=timezone.utc),
        },
    ]
    bars = ticks_to_bars(ticks, interval="1m")
    assert len(bars) == 1
    assert bars[0].open == 1.0
    assert bars[0].close == 1.0
