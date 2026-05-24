"""Clean, normalize, resample OHLCV."""

import pandas as pd


class OHLCVProcessor:
    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.dropna()

