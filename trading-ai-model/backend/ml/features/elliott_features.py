"""
ml/features/elliott_features.py
Simplified Elliott Wave context features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)
    if len(df) < 30:
        features["ew_impulse_strength"] = 0.0
        features["ew_correction_depth"] = 0.0
        features["ew_is_impulse"] = 0
        features["ew_is_correction"] = 0
        return features

    close = df["close"]

    long_return = float(close.pct_change(30).iloc[-1]) * 100
    short_return = float(close.pct_change(5).iloc[-1]) * 100
    mid_return = float(close.pct_change(15).iloc[-1]) * 100

    trend_aligned = (long_return > 0 and mid_return > 0 and short_return > 0) or (
        long_return < 0 and mid_return < 0 and short_return < 0
    )
    impulse_strength = abs(long_return) / (abs(short_return) + abs(mid_return) + 1e-10)
    features["ew_impulse_strength"] = round(float(min(impulse_strength, 5.0)), 4)
    features["ew_is_impulse"] = int(trend_aligned and impulse_strength > 1.5)

    correction_depth = 0.0
    if long_return != 0:
        correction_depth = -short_return / (abs(long_return) + 1e-10)
    features["ew_correction_depth"] = round(float(np.clip(correction_depth, -1, 1)), 4)
    features["ew_is_correction"] = int(correction_depth > 0.382 and correction_depth < 0.786)

    return features
