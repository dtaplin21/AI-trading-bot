"""Classifies impulsive vs corrective moves."""

import pandas as pd


class ImpulseCorrectionClassifier:
    def classify(self, ohlcv: pd.DataFrame) -> str:
        return "unknown"

