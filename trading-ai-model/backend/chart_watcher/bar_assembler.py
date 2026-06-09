"""
chart_watcher/bar_assembler.py

Assembles ticks or 1-minute candles into clean OHLCV bars
for every configured timeframe (1m, 3m, 5m, 15m, 1h, …).

Two input modes:
  tick mode   — raw trade prints → all intraday timeframes (< 1 day)
  candle mode — pre-built OHLCV candles from broker/feed → configured TFs

Anti-look-ahead: a bar is only emitted when superseded by a newer period.
The current open bar is never forwarded to the pipeline.

Env:
  WATCHER_TIMEFRAMES   comma-separated, e.g. "1m,5m,15m,1h"
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional, Union

from pipeline.schemas import OHLCV

logger = logging.getLogger(__name__)

_tf_env = os.getenv("WATCHER_TIMEFRAMES", "1m,5m,15m,1h")
DEFAULT_TIMEFRAMES = [tf.strip() for tf in _tf_env.split(",") if tf.strip()]

TF_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
}

# Tick mode rolls up into every intraday timeframe (strictly under one day)
INTRADAY_TIMEFRAMES: list[str] = sorted(
    (tf for tf, mins in TF_MINUTES.items() if mins < 24 * 60),
    key=lambda t: TF_MINUTES[t],
)

OnBarComplete = Callable[[OHLCV], Union[Awaitable[None], None]]
_ONE_DAY_MINUTES = 24 * 60


def timeframe_to_seconds(tf: str) -> int:
    mins = TF_MINUTES.get(tf.strip().lower())
    if mins is None:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return mins * 60


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _floor_timestamp(dt: datetime, tf_minutes: int) -> datetime:
    """Snap a datetime down to the nearest tf_minutes wall-clock boundary."""
    dt = _utc(dt)
    total_minutes = dt.hour * 60 + dt.minute
    floored = (total_minutes // tf_minutes) * tf_minutes
    return dt.replace(
        hour=floored // 60,
        minute=floored % 60,
        second=0,
        microsecond=0,
    )


class OpenBar:
    """One partially-built bar for one symbol+timeframe."""

    __slots__ = (
        "symbol",
        "timeframe",
        "open_time",
        "close_time",
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
        tf_minutes: int,
        first_price: float,
        first_volume: float = 0.0,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.open_time = open_time
        self.close_time = open_time + timedelta(minutes=tf_minutes)
        self.open = first_price
        self.high = first_price
        self.low = first_price
        self.close = first_price
        self.volume = first_volume
        self.tick_count = 1

    def update(self, price: float, volume: float = 0.0) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1

    def update_from_candle(self, bar: OHLCV) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.volume += bar.volume
        self.tick_count += 1

    def to_ohlcv(self) -> OHLCV:
        return OHLCV(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=self.open_time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class BarAssembler:
    """
    Converts raw price updates into completed OHLCV bars.

    Anti-look-ahead: bars are only emitted when superseded by a newer period.
    """

    def __init__(
        self,
        symbol: str,
        timeframes: list[str] | None = None,
        on_bar_complete: Optional[OnBarComplete] = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframes = timeframes or list(DEFAULT_TIMEFRAMES)
        self._on_complete = on_bar_complete
        self._open_bars: dict[str, OpenBar] = {}
        self._history: dict[str, list[OHLCV]] = defaultdict(list)

        logger.debug(
            "BarAssembler[%s]: candle_timeframes=%s tick_timeframes=%s",
            self.symbol,
            self.timeframes,
            INTRADAY_TIMEFRAMES,
        )

    async def on_tick(
        self,
        price: float,
        volume: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> list[OHLCV]:
        """
        Process one tick into every intraday timeframe (< 1 day).
        Returns completed bars emitted this tick (may be empty).
        """
        ts = _utc(timestamp or datetime.now(tz=timezone.utc))
        completed: list[OHLCV] = []

        for tf in INTRADAY_TIMEFRAMES:
            tf_minutes = TF_MINUTES[tf]
            bar_open_time = _floor_timestamp(ts, tf_minutes)
            finished = await self._apply_price(
                tf, tf_minutes, bar_open_time, price, volume, from_candle=False
            )
            if finished:
                completed.append(finished)

        return completed

    async def on_candle(self, bar: OHLCV) -> list[OHLCV]:
        """Accept a completed 1m candle; aggregate into configured higher TFs."""
        if bar.symbol.upper() != self.symbol:
            bar = bar.model_copy(update={"symbol": self.symbol})

        completed: list[OHLCV] = []

        for tf in self.timeframes:
            if tf == "1m":
                bar_1m = bar.model_copy(update={"timeframe": "1m"})
                await self._emit(bar_1m)
                completed.append(bar_1m)
                continue

            tf_minutes = TF_MINUTES.get(tf)
            if not tf_minutes or tf_minutes >= _ONE_DAY_MINUTES:
                continue

            bar_open_time = _floor_timestamp(bar.timestamp, tf_minutes)
            finished = await self._apply_price(
                tf,
                tf_minutes,
                bar_open_time,
                bar.open,
                bar.volume,
                from_candle=True,
                candle=bar,
            )
            if finished:
                completed.append(finished)

        return completed

    async def _apply_price(
        self,
        tf: str,
        tf_minutes: int,
        bar_open_time: datetime,
        price: float,
        volume: float,
        *,
        from_candle: bool,
        candle: Optional[OHLCV] = None,
    ) -> Optional[OHLCV]:
        open_bar = self._open_bars.get(tf)

        if open_bar is None:
            ob = OpenBar(self.symbol, tf, bar_open_time, tf_minutes, price, volume)
            if from_candle and candle:
                ob.high = candle.high
                ob.low = candle.low
                ob.close = candle.close
            self._open_bars[tf] = ob
            return None

        if bar_open_time > open_bar.open_time:
            finished = open_bar.to_ohlcv()
            await self._emit(finished)

            nb = OpenBar(self.symbol, tf, bar_open_time, tf_minutes, price, volume)
            if from_candle and candle:
                nb.high = candle.high
                nb.low = candle.low
                nb.close = candle.close
            self._open_bars[tf] = nb
            return finished

        if from_candle and candle:
            open_bar.update_from_candle(candle)
        else:
            open_bar.update(price, volume)
        return None

    async def _emit(self, bar: OHLCV) -> None:
        hist = self._history[bar.timeframe]
        hist.append(bar)
        if len(hist) > 600:
            self._history[bar.timeframe] = hist[-500:]

        if self._on_complete:
            if asyncio.iscoroutinefunction(self._on_complete):
                await self._on_complete(bar)
            else:
                self._on_complete(bar)

    async def flush(self) -> list[OHLCV]:
        """Force-emit all open bars (end of replay / shutdown)."""
        flushed: list[OHLCV] = []
        for open_bar in list(self._open_bars.values()):
            if open_bar.tick_count > 0:
                bar = open_bar.to_ohlcv()
                await self._emit(bar)
                flushed.append(bar)
        self._open_bars.clear()
        return flushed

    def current_bar(self, timeframe: str) -> Optional[OHLCV]:
        """Open bar peek — dashboard only; never feed to pipeline."""
        ob = self._open_bars.get(timeframe)
        return ob.to_ohlcv() if ob else None

    def get_history(self, timeframe: str, max_bars: int = 500) -> list[OHLCV]:
        return self._history.get(timeframe, [])[-max_bars:]

    def record_completed(self, bar: OHLCV) -> None:
        """Append a finalized bar to history without firing on_bar_complete."""
        hist = self._history[bar.timeframe]
        hist.append(bar)
        if len(hist) > 600:
            self._history[bar.timeframe] = hist[-500:]


class MultiSymbolAssembler:
    """One BarAssembler per symbol."""

    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str] | None = None,
        on_bar_complete: Optional[OnBarComplete] = None,
    ) -> None:
        tfs = timeframes or list(DEFAULT_TIMEFRAMES)
        self._assemblers: dict[str, BarAssembler] = {
            sym.upper(): BarAssembler(sym, tfs, on_bar_complete)
            for sym in symbols
        }

    def get(self, symbol: str) -> Optional[BarAssembler]:
        return self._assemblers.get(symbol.upper())

    def symbols(self) -> list[str]:
        return list(self._assemblers.keys())

    async def flush_all(self) -> dict[str, list[OHLCV]]:
        result: dict[str, list[OHLCV]] = {}
        for sym, asm in self._assemblers.items():
            result[sym] = await asm.flush()
        return result
