"""Fractal swing detection."""

import pandas as pd


class FractalService:
    def detect(self, ohlcv: pd.DataFrame, window: int = 2) -> dict:
        return {"fractal_up": False, "fractal_down": False}

