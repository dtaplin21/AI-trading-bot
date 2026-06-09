"""
ml/features/gann_features.py
Gann square of 9 and geometric angle levels.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _gann_sq9_levels(price: float, n_levels: int = 8) -> list[float]:
    """Compute Gann Square of 9 levels around a price."""
    sqrt_price = np.sqrt(price)
    levels: list[float] = []
    for i in range(-n_levels // 2, n_levels // 2 + 1):
        level = (sqrt_price + i * 0.25) ** 2
        if level > 0:
            levels.append(round(float(level), 5))
    return sorted(levels)


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)
    if len(df) < 5:
        return features

    close = float(df["close"].iloc[-1])

    gann_levels = _gann_sq9_levels(close)
    distances = [abs(close - lvl) / (close + 1e-10) * 100 for lvl in gann_levels]
    nearest_dist = min(distances)

    features["gann_sq9_nearest_dist"] = round(nearest_dist, 4)
    features["gann_sq9_at_level"] = int(nearest_dist < 0.15)

    if len(df) >= 2:
        bars_from_low = 20
        window = df.tail(min(bars_from_low, len(df)))
        base_price = float(window["low"].min())
        current_bar = len(window) - 1
        time_units = current_bar + 1

        gann_1x1_level = base_price + time_units * (
            float(window["high"].max() - base_price) / bars_from_low
        )
        features["gann_1x1_dist_pct"] = round(
            abs(close - gann_1x1_level) / (close + 1e-10) * 100, 4
        )
        features["gann_at_1x1"] = int(features["gann_1x1_dist_pct"] < 0.20)
    else:
        features["gann_1x1_dist_pct"] = 5.0
        features["gann_at_1x1"] = 0

    return features
