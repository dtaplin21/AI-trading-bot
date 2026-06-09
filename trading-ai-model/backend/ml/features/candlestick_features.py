"""
ml/features/candlestick_features.py
Detects classic candlestick reversal patterns.
"""
from __future__ import annotations

import pandas as pd


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    """Detect candlestick patterns and add to layer_output."""
    features = dict(layer_output)
    if len(df) < 3:
        return features

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values

    i = len(df) - 1
    body = abs(c[i] - o[i])
    total = h[i] - l[i] + 1e-10
    body_pct = body / total

    upper_wick = h[i] - max(o[i], c[i])
    lower_wick = min(o[i], c[i]) - l[i]
    is_bull = int(c[i] > o[i])

    features["cs_doji"] = int(body_pct < 0.10)

    features["cs_hammer"] = int(
        lower_wick > 2 * body and upper_wick < body and not is_bull
        or lower_wick > 2 * body and is_bull
    )
    features["cs_shooting_star"] = int(upper_wick > 2 * body and lower_wick < body)

    features["cs_marubozu"] = int(body_pct > 0.90)

    if i > 0:
        prev_body_top = max(o[i - 1], c[i - 1])
        prev_body_bot = min(o[i - 1], c[i - 1])
        curr_body_top = max(o[i], c[i])
        curr_body_bot = min(o[i], c[i])
        features["cs_bull_engulf"] = int(
            is_bull
            and curr_body_top > prev_body_top
            and curr_body_bot < prev_body_bot
            and not int(c[i - 1] > o[i - 1])
        )
        features["cs_bear_engulf"] = int(
            not is_bull
            and curr_body_top > prev_body_top
            and curr_body_bot < prev_body_bot
            and int(c[i - 1] > o[i - 1])
        )
    else:
        features["cs_bull_engulf"] = 0
        features["cs_bear_engulf"] = 0

    if i > 0:
        features["cs_inside_bar"] = int(h[i] < h[i - 1] and l[i] > l[i - 1])
    else:
        features["cs_inside_bar"] = 0

    features["cs_bull_pin"] = int(lower_wick > 2 * upper_wick and lower_wick > 2 * body)
    features["cs_bear_pin"] = int(upper_wick > 2 * lower_wick and upper_wick > 2 * body)

    features["cs_body_ratio"] = round(float(body_pct), 4)
    features["cs_wick_ratio"] = round(float((upper_wick + lower_wick) / total), 4)
    features["cs_is_bullish"] = is_bull

    return features
