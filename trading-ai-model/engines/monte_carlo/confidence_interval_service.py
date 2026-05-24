"""Confidence intervals for outcome ranges."""

import numpy as np


class ConfidenceIntervalService:
    def ci(self, samples: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
        lo = (alpha / 2) * 100
        hi = (1 - alpha / 2) * 100
        return float(np.percentile(samples, lo)), float(np.percentile(samples, hi))

