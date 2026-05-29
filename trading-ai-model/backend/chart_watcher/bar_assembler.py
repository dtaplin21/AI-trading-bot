"""Multi-timeframe bar assembly from 1m (or native) candles."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from pipeline.schemas import OHLCV

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAMES = ["1m", "5m", "15m", "1h"]

OnBarComplete = Callable[[OHLCV], Awaitable[None]]


def timeframe_to_seconds(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1]) * 60
    if tf.endswith("h"):
        return int(tf[:-1]) * 3600
    if tf.endswith("d"):
        return int(tf[:-1]) * 86400
    raise ValueError(f"Unsupported timeframe: {tf}")


def _bucket_start(ts: datetime, period_sec: int) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    epoch = int(ts.timestamp())
    aligned = epoch - (epoch % period_sec)
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


class _Bucket:
    __slots__ = ("open", "high", "low", "close", "volume", "start", "count")

    def __init__(self, bar: OHLCV) -> None:
        self.open = bar.open
        self.high = bar.high
        self.low = bar.low
        self.close = bar.close
        self.volume = bar.volume
        self.start = bar.timestamp
        self.count = 1

    def update(self, bar: OHLCV) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.volume += bar.volume
        self.count += 1

    def to_ohlcv(self, symbol: str, timeframe: str) -> OHLCV:
        return OHLCV(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=self.start,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class BarAssembler:
    """
    Ingests 1m bars (or native-TF bars) and emits completed higher-timeframe bars.
    """

    def __init__(
        self,
        symbol: str,
        timeframes: list[str] | None = None,
        on_bar_complete: Optional[OnBarComplete] = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframes = timeframes or list(DEFAULT_TIMEFRAMES)
        self.on_bar_complete = on_bar_complete

        self._periods: dict[str, int] = {}
        for tf in self.timeframes:
            self._periods[tf] = timeframe_to_seconds(tf)

        self._active: dict[str, Optional[_Bucket]] = {tf: None for tf in self.timeframes}
        self._history: dict[str, list[OHLCV]] = defaultdict(list)

    async def on_candle(self, bar: OHLCV) -> None:
        """Feed one candle; may emit zero or more completed aggregated bars."""
        if bar.symbol.upper() != self.symbol:
            bar = bar.model_copy(update={"symbol": self.symbol})

        # If incoming bar is already a higher TF, route directly when listed
        if bar.timeframe in self._periods and bar.timeframe != "1m":
            completed = bar
            await self._emit(completed)
            return

        # Always process as 1m source for aggregation
        source = bar.model_copy(update={"timeframe": "1m"})
        self._append_history("1m", source)

        for tf, period in self._periods.items():
            if tf == "1m":
                await self._emit(source)
                continue

            bucket_start = _bucket_start(source.timestamp, period)
            active = self._active.get(tf)

            if active is None:
                b = _Bucket(source)
                b.start = bucket_start
                self._active[tf] = b
                continue

            if bucket_start == active.start:
                active.update(source)
                continue

            # Bucket closed — emit and start new
            completed = active.to_ohlcv(self.symbol, tf)
            self._append_history(tf, completed)
            await self._emit(completed)
            nb = _Bucket(source)
            nb.start = bucket_start
            self._active[tf] = nb

    async def flush(self) -> None:
        """Emit any open buckets (end of stream / shutdown)."""
        for tf, active in list(self._active.items()):
            if active is None:
                continue
            completed = active.to_ohlcv(self.symbol, tf)
            self._append_history(tf, completed)
            await self._emit(completed)
            self._active[tf] = None

    def get_history(self, timeframe: str, max_bars: int = 500) -> list[OHLCV]:
        hist = self._history.get(timeframe, [])
        return hist[-max_bars:]

    def _append_history(self, timeframe: str, bar: OHLCV) -> None:
        hist = self._history[timeframe]
        hist.append(bar)
        if len(hist) > 600:
            self._history[timeframe] = hist[-500:]

    async def _emit(self, bar: OHLCV) -> None:
        if self.on_bar_complete:
            await self.on_bar_complete(bar)


class MultiSymbolAssembler:
    """One BarAssembler per symbol."""

    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str] | None = None,
        on_bar_complete: Optional[OnBarComplete] = None,
    ) -> None:
        self._assemblers: dict[str, BarAssembler] = {
            sym.upper(): BarAssembler(sym, timeframes, on_bar_complete)
            for sym in symbols
        }

    def get(self, symbol: str) -> Optional[BarAssembler]:
        return self._assemblers.get(symbol.upper())

    async def flush_all(self) -> None:
        for asm in self._assemblers.values():
            await asm.flush()
