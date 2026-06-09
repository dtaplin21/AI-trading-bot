"""
ml/features/fractal_features.py
Detects Williams Fractals — local swing highs and lows.
"""
from __future__ import annotations

import pandas as pd


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)
    if len(df) < 10:
        return features

    highs = df["high"].values
    lows = df["low"].values
    close = float(df["close"].iloc[-1])
    n = len(df)

    def is_fractal_high(i: int) -> bool:
        if i < 2 or i > n - 3:
            return False
        return (
            highs[i] > highs[i - 1]
            and highs[i] > highs[i - 2]
            and highs[i] > highs[i + 1]
            and highs[i] > highs[i + 2]
        )

    def is_fractal_low(i: int) -> bool:
        if i < 2 or i > n - 3:
            return False
        return (
            lows[i] < lows[i - 1]
            and lows[i] < lows[i - 2]
            and lows[i] < lows[i + 1]
            and lows[i] < lows[i + 2]
        )

    lookback = min(50, n - 3)
    fractal_highs = [highs[i] for i in range(n - 3 - lookback, n - 3) if is_fractal_high(i)]
    fractal_lows = [lows[i] for i in range(n - 3 - lookback, n - 3) if is_fractal_low(i)]

    if fractal_highs:
        nearest_fh = min(fractal_highs, key=lambda x: abs(x - close))
        features["fractal_high_dist_pct"] = round(
            abs(close - nearest_fh) / (close + 1e-10) * 100, 4
        )
        features["fractal_at_high"] = int(features["fractal_high_dist_pct"] < 0.10)
        features["fractal_high_count"] = len(fractal_highs)
    else:
        features["fractal_high_dist_pct"] = 5.0
        features["fractal_at_high"] = 0
        features["fractal_high_count"] = 0

    if fractal_lows:
        nearest_fl = min(fractal_lows, key=lambda x: abs(x - close))
        features["fractal_low_dist_pct"] = round(
            abs(close - nearest_fl) / (close + 1e-10) * 100, 4
        )
        features["fractal_at_low"] = int(features["fractal_low_dist_pct"] < 0.10)
        features["fractal_low_count"] = len(fractal_lows)
    else:
        features["fractal_low_dist_pct"] = 5.0
        features["fractal_at_low"] = 0
        features["fractal_low_count"] = 0

    return features
