"""
ml/features/markov_features.py
Markov chain state transition probabilities for market states.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _classify_bar(pct_change: float) -> int:
    """Classify a bar return into a state: 0=down, 1=flat, 2=up."""
    if pct_change > 0.05:
        return 2
    if pct_change < -0.05:
        return 0
    return 1


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)
    if len(df) < 20:
        features["markov_p_up"] = 0.33
        features["markov_p_down"] = 0.33
        features["markov_p_flat"] = 0.34
        features["markov_trend_persist"] = 0.5
        return features

    returns = df["close"].pct_change().dropna() * 100
    states = [_classify_bar(r) for r in returns.values]

    n = 3
    trans = np.ones((n, n)) * 0.1
    for i in range(len(states) - 1):
        trans[states[i], states[i + 1]] += 1

    row_sums = trans.sum(axis=1, keepdims=True)
    trans = trans / (row_sums + 1e-10)

    current_state = states[-1] if states else 1
    p_up = float(trans[current_state, 2])
    p_down = float(trans[current_state, 0])
    p_flat = float(trans[current_state, 1])
    trend_persist = float(trans[current_state, current_state])

    features["markov_p_up"] = round(p_up, 4)
    features["markov_p_down"] = round(p_down, 4)
    features["markov_p_flat"] = round(p_flat, 4)
    features["markov_trend_persist"] = round(trend_persist, 4)
    features["markov_current_state"] = current_state

    return features
