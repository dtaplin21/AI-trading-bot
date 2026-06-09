"""
Aggregate ticks into OHLCV bars.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Union

import pandas as pd

from data.loaders.tick_data_loader import Tick
from pipeline.schemas import OHLCV

_INTERVAL_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}


class TickAggregator:
    """Convert a stream/batch of ticks into completed OHLCV bars."""

    def to_bars(
        self,
        ticks: Iterable[Union[Tick, dict]],
        interval: str = "1m",
        symbol: str | None = None,
    ) -> list[OHLCV]:
        rows = []
        sym = (symbol or "").upper()

        for tick in ticks:
            if isinstance(tick, Tick):
                sym = tick.symbol
                rows.append(
                    {
                        "time": tick.timestamp,
                        "price": tick.price,
                        "volume": tick.size,
                    }
                )
            elif isinstance(tick, dict):
                sym = str(tick.get("symbol", sym)).upper()
                ts = tick.get("timestamp") or tick.get("time")
                if isinstance(ts, str):
                    ts = pd.Timestamp(ts, tz="UTC").to_pydatetime()
                rows.append(
                    {
                        "time": ts,
                        "price": float(tick["price"]),
                        "volume": float(tick.get("size", tick.get("volume", 0))),
                    }
                )

        if not rows or not sym:
            return []

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time").sort_index()

        rule = _INTERVAL_MAP.get(interval, interval)
        ohlcv = df["price"].resample(rule).ohlc()
        vol = df["volume"].resample(rule).sum()
        ohlcv["volume"] = vol
        ohlcv = ohlcv.dropna(subset=["open"])

        bars: list[OHLCV] = []
        for ts, row in ohlcv.iterrows():
            t = pd.Timestamp(ts).to_pydatetime()
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            bars.append(
                OHLCV(
                    symbol=sym,
                    timeframe=interval,
                    timestamp=t,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                )
            )
        return bars

    def aggregate_live(
        self,
        ticks_by_symbol: dict[str, list[Tick]],
        interval: str = "1m",
    ) -> dict[str, list[OHLCV]]:
        """Batch aggregate multiple symbols."""
        return {
            sym: self.to_bars(tick_list, interval=interval, symbol=sym)
            for sym, tick_list in ticks_by_symbol.items()
            if tick_list
        }

    def bucket_ticks(self, ticks: Iterable[Tick], interval: str = "1m") -> dict[datetime, list[Tick]]:
        """Group ticks by bar open time without OHLCV aggregation."""
        buckets: dict[datetime, list[Tick]] = defaultdict(list)
        minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}.get(interval, 1)

        for tick in ticks:
            ts = tick.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            total = ts.hour * 60 + ts.minute
            floored = (total // minutes) * minutes
            key = ts.replace(
                hour=floored // 60,
                minute=floored % 60,
                second=0,
                microsecond=0,
            )
            buckets[key].append(tick)
        return dict(buckets)
