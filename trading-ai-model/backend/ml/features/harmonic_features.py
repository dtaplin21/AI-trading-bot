"""
ml/features/harmonic_features.py
Detects harmonic price patterns (ABCD, Bat, Gartley).
"""
from __future__ import annotations

import pandas as pd

PATTERNS = {
    "abcd": {"BC_AB": (0.382, 0.886), "CD_BC": (1.13, 2.618)},
    "gartley": {"XA_AB": (0.618,), "BC_AB": (0.382, 0.886), "CD_XA": (0.786,)},
    "bat": {"XA_AB": (0.382, 0.500), "BC_AB": (0.382, 0.886), "CD_XA": (0.886,)},
}
TOLERANCE = 0.05


def _ratio_match(ratio: float, targets: tuple[float, ...], tol: float = TOLERANCE) -> bool:
    return any(abs(ratio - t) <= tol for t in targets)


def extract(df: pd.DataFrame, layer_output: dict) -> dict:
    features = dict(layer_output)

    features["harm_abcd_bull"] = 0
    features["harm_abcd_bear"] = 0
    features["harm_gartley"] = 0
    features["harm_bat"] = 0
    features["harm_any_pattern"] = 0

    if len(df) < 10:
        return features

    close = float(df["close"].iloc[-1])

    try:
        h1 = float(df["high"].tail(10).max())
        l1 = float(df["low"].tail(10).min())
        h2 = float(df["high"].tail(5).max())
        l2 = float(df["low"].tail(5).min())

        if h1 > l1 and h2 > l2 and h1 > 0:
            ab = abs(h1 - l1)
            bc = abs(h2 - l2)
            if ab > 0:
                bc_ab = bc / ab
                cd = abs(close - l2)
                if bc > 0:
                    cd_bc = cd / bc
                    if _ratio_match(bc_ab, (0.618, 0.786)) and _ratio_match(
                        cd_bc, (1.272, 1.618)
                    ):
                        features["harm_abcd_bull"] = 1
                        features["harm_any_pattern"] = 1
                    if _ratio_match(bc_ab, (0.382, 0.500)) and _ratio_match(
                        cd_bc, (1.618, 2.0)
                    ):
                        features["harm_abcd_bear"] = 1
                        features["harm_any_pattern"] = 1
    except Exception:
        pass

    return features
