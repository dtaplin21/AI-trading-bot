"""OHLCV ingest — loads from TimescaleDB when available."""

import pandas as pd

from data.storage.timescale_store import TimescaleStore


class MarketDataLoader:
    def __init__(self, store: TimescaleStore | None = None):
        self.store = store or TimescaleStore()

    def load(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        timeframe: str = "5m",
        limit: int = 500,
    ) -> pd.DataFrame:
        if self.store.available:
            df = self.store.load_ohlcv(symbol, timeframe, limit=limit, start=start or None, end=end or None)
            if not df.empty:
                return df
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
