"""
data/processors/tick_aggregator.py

Aggregates raw ticks into OHLCV bars at a fixed timeframe.

Was: return []
Now: real tick aggregation with proper bar open/close timing

Connects to:
  - TickDataLoader — consumes tick stream
  - TimeseriesStore — receives completed bars to persist
  - ChartWatchRunner — calls update() per tick, get_completed() per bar close

How it works:
  Every tick updates the current open bar.
  When the bar period ends (e.g. 60 seconds for 1m bars),
  the bar is finalized and a new one starts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional, Union

from data.loaders.tick_data_loader import Tick
from pipeline.schemas import OHLCV

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


class Bar:
    """An OHLCV bar being assembled from ticks."""

    __slots__ = (
        "symbol",
        "timeframe",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "tick_count",
    )

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        open_time: datetime,
        first_price: float,
        size: float = 0,
    ):
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.open_time = _utc(open_time)
        self.open = first_price
        self.high = first_price
        self.low = first_price
        self.close = first_price
        self.volume = size
        self.tick_count = 1

    def update(self, price: float, size: float = 0) -> None:
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += size
        self.tick_count += 1

    def to_dict(self) -> dict:
        return {
            "time": self.open_time,
            "open": round(self.open, 8),
            "high": round(self.high, 8),
            "low": round(self.low, 8),
            "close": round(self.close, 8),
            "volume": round(self.volume, 8),
        }

    def to_ohlcv(self) -> OHLCV:
        d = self.to_dict()
        return OHLCV(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=d["time"],
            open=d["open"],
            high=d["high"],
            low=d["low"],
            close=d["close"],
            volume=d["volume"],
        )

    def __repr__(self):
        return (
            f"Bar({self.symbol} {self.timeframe} {self.open_time} "
            f"O={self.open} H={self.high} L={self.low} C={self.close} V={self.volume})"
        )


class TickAggregator:
    """
    Aggregates ticks into OHLCV bars for one symbol and timeframe.

    Usage:
        agg = TickAggregator("EURUSD", "1m")
        completed = agg.update(price=1.0852, size=1.0, timestamp=datetime.now())
        if completed:
            print(f"Bar complete: {completed}")
    """

    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.period_secs = TIMEFRAME_SECONDS.get(timeframe, 60)
        self._current: Optional[Bar] = None
        self._completed: list[Bar] = []

    def update(
        self,
        price: float,
        size: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> Optional[dict]:
        """
        Process one tick. Returns completed bar dict if the bar period closed,
        otherwise returns None.
        """
        if timestamp is None:
            timestamp = datetime.now(tz=timezone.utc)
        timestamp = _utc(timestamp)

        bar_open_ts = self._bar_open_time(timestamp)

        if self._current is None:
            self._current = Bar(self.symbol, self.timeframe, bar_open_ts, price, size)
            return None

        if bar_open_ts == self._current.open_time:
            self._current.update(price, size)
            return None

        completed = self._current
        self._current = Bar(self.symbol, self.timeframe, bar_open_ts, price, size)
        self._completed.append(completed)

        logger.debug(
            "%s/%s: bar complete O=%.5f H=%.5f L=%.5f C=%.5f V=%.2f ticks=%d",
            self.symbol,
            self.timeframe,
            completed.open,
            completed.high,
            completed.low,
            completed.close,
            completed.volume,
            completed.tick_count,
        )

        return completed.to_dict()

    def get_current(self) -> Optional[dict]:
        """Get the current open (incomplete) bar as a dict."""
        return self._current.to_dict() if self._current else None

    def get_completed(self) -> list[dict]:
        """Return completed bars buffered since last flush (does not clear)."""
        return [b.to_dict() for b in self._completed]

    def flush_completed(self) -> list[dict]:
        """Return and clear all completed bars."""
        bars = [b.to_dict() for b in self._completed]
        self._completed.clear()
        return bars

    def _bar_open_time(self, ts: datetime) -> datetime:
        """Snap a timestamp to the bar's open time."""
        epoch = int(ts.timestamp())
        snapped = (epoch // self.period_secs) * self.period_secs
        return datetime.fromtimestamp(snapped, tz=timezone.utc)


class MultiSymbolAggregator:
    """
    Manages one TickAggregator per symbol+timeframe combination.
    Used by ChartWatchRunner to handle all watched symbols.
    """

    def __init__(self, timeframes: Optional[list[str]] = None):
        self.timeframes = timeframes or ["1m", "5m", "15m", "1h"]
        self._aggs: dict[str, TickAggregator] = {}

    def update(
        self,
        symbol: str,
        price: float,
        size: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Process one tick for a symbol across all timeframes.
        Returns list of completed bars (one per timeframe that closed).
        """
        sym = symbol.upper()
        completed: list[dict] = []
        for tf in self.timeframes:
            key = f"{sym}:{tf}"
            if key not in self._aggs:
                self._aggs[key] = TickAggregator(sym, tf)

            bar = self._aggs[key].update(price, size, timestamp)
            if bar:
                bar["symbol"] = sym
                bar["timeframe"] = tf
                completed.append(bar)

        return completed

    def get_current(self, symbol: str, timeframe: str) -> Optional[dict]:
        key = f"{symbol.upper()}:{timeframe}"
        agg = self._aggs.get(key)
        return agg.get_current() if agg else None

    def get_completed(self, symbol: str | None = None) -> list[dict]:
        """Return buffered completed bars, optionally filtered by symbol."""
        out: list[dict] = []
        for key, agg in self._aggs.items():
            sym = key.split(":", 1)[0]
            if symbol and sym != symbol.upper():
                continue
            for bar in agg.get_completed():
                bar = dict(bar)
                bar["symbol"] = sym
                bar["timeframe"] = key.split(":", 1)[1]
                out.append(bar)
        return out


def bar_dict_to_ohlcv(bar: dict) -> OHLCV:
    """Convert aggregator output dict to pipeline OHLCV."""
    ts = bar["time"]
    if not isinstance(ts, datetime):
        ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return OHLCV(
        symbol=str(bar["symbol"]).upper(),
        timeframe=str(bar["timeframe"]),
        timestamp=ts,
        open=float(bar["open"]),
        high=float(bar["high"]),
        low=float(bar["low"]),
        close=float(bar["close"]),
        volume=float(bar.get("volume", 0)),
    )


def ticks_to_bars(
    ticks: Iterable[Union[Tick, dict]],
    interval: str = "1m",
    symbol: str | None = None,
) -> list[OHLCV]:
    """Batch-convert ticks to OHLCV bars (replay / backfill helper)."""
    agg: TickAggregator | None = None
    bars: list[OHLCV] = []

    for tick in ticks:
        if isinstance(tick, Tick):
            sym = tick.symbol.upper()
            price = tick.price
            size = tick.size
            ts = tick.timestamp
        else:
            sym = str(tick.get("symbol", symbol or "")).upper()
            price = float(tick["price"])
            size = float(tick.get("size", tick.get("volume", 0)))
            raw_ts = tick.get("timestamp") or tick.get("time")
            ts = raw_ts if isinstance(raw_ts, datetime) else datetime.now(tz=timezone.utc)

        if not sym:
            continue
        if agg is None or agg.symbol != sym or agg.timeframe != interval:
            agg = TickAggregator(sym, interval)

        completed = agg.update(price, size, ts)
        if completed:
            completed["symbol"] = sym
            completed["timeframe"] = interval
            bars.append(bar_dict_to_ohlcv(completed))

    return bars
