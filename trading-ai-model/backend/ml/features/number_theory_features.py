"""
ml/features/number_theory_features.py
Tesla 3/6/9 and sacred number proximity features.
"""
from __future__ import annotations

import pandas as pd


def _nearest_369(price: float) -> float:
    """Distance to nearest number whose digits sum to 3, 6, or 9."""
    candidates: list[int] = []
    base = int(price)
    for offset in range(-20, 21):
        n = base + offset
        if n <= 0:
            continue
        digit_sum = sum(int(d) for d in str(abs(n)))
        while digit_sum > 9:
            digit_sum = sum(int(d) for d in str(digit_sum))
        if digit_sum in (3, 6, 9):
            candidates.append(n)
    if not candidates:
        return 99.0
    nearest = min(candidates, key=lambda x: abs(x - price))
    return abs(price - nearest) / (price + 1e-10) * 100


def _phi_levels(price: float) -> list[float]:
    """Golden ratio (phi=1.618) based price levels."""
    phi = 1.6180339887
    return [
        round(price / phi, 5),
        round(price * phi, 5),
        round(price / (phi**2), 5),
        round(price * (phi**2), 5),
    ]


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)
    if len(df) < 2:
        return features

    close = float(df["close"].iloc[-1])

    dist_369 = _nearest_369(close)
    features["nt_369_dist_pct"] = round(float(dist_369), 4)
    features["nt_at_369"] = int(dist_369 < 0.10)

    phi_lvls = _phi_levels(close)
    phi_dists = [abs(close - lvl) / (close + 1e-10) * 100 for lvl in phi_lvls]
    features["nt_phi_nearest_dist"] = round(float(min(phi_dists)), 4)
    features["nt_at_phi"] = int(min(phi_dists) < 0.10)

    round_100 = round(close / 100) * 100
    round_1000 = round(close / 1000) * 1000
    features["nt_round_100_dist"] = round(abs(close - round_100) / (close + 1e-10) * 100, 4)
    features["nt_round_1000_dist"] = round(abs(close - round_1000) / (close + 1e-10) * 100, 4)
    features["nt_at_round_100"] = int(features["nt_round_100_dist"] < 0.05)
    features["nt_at_round_1000"] = int(features["nt_round_1000_dist"] < 0.10)

    return features
