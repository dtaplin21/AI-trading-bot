"""Wick rejection scoring and multi-candle wick confluence."""

import pandas as pd


class WickAnalysisService:
    def analyze(self, ohlcv: pd.DataFrame) -> dict:
        row = ohlcv.iloc[-1]
        rng = row["high"] - row["low"] or 1e-9
        return {
            "upper_wick_rejection": (row["high"] - max(row["open"], row["close"])) / rng,
            "lower_wick_rejection": (min(row["open"], row["close"]) - row["low"]) / rng,
        }

