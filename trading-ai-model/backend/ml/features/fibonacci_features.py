"""
ml/features/fibonacci_features.py
Computes Fibonacci retracement levels and proximity scores.
"""
from __future__ import annotations

import pandas as pd

FIB_LEVELS = [0.0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.0]
FIB_EXTENSIONS = [1.272, 1.414, 1.618, 2.0, 2.618]


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)
    if len(df) < 20:
        return features

    lookback = min(20, len(df))
    window = df.tail(lookback)
    swing_high = float(window["high"].max())
    swing_low = float(window["low"].min())
    current = float(df["close"].iloc[-1])
    price_range = swing_high - swing_low + 1e-10

    fib_prices = {
        f"fib_{int(level * 1000)}": swing_low + level * price_range for level in FIB_LEVELS
    }

    distances = {
        name: abs(current - price) / (current + 1e-10) * 100
        for name, price in fib_prices.items()
    }
    nearest_name, nearest_dist = min(distances.items(), key=lambda item: item[1])

    features["fib_nearest_level"] = round(float(fib_prices[nearest_name]), 6)
    features["fib_nearest_dist_pct"] = round(float(nearest_dist), 4)
    features["fib_at_level"] = int(nearest_dist < 0.10)
    features["fib_at_618"] = int(distances.get("fib_618", 999) < 0.15)
    features["fib_at_382"] = int(distances.get("fib_382", 999) < 0.15)
    features["fib_at_500"] = int(distances.get("fib_500", 999) < 0.10)

    features["fib_range_position"] = round(float((current - swing_low) / price_range), 4)

    return features
