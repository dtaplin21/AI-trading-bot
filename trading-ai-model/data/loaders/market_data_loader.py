"""OHLCV ingest (live + historical)."""

import pandas as pd


class MarketDataLoader:
    def load(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

