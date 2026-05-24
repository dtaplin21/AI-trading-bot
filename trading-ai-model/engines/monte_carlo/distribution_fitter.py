"""Fit return distributions."""

import numpy as np
from scipy import stats


class DistributionFitter:
    def fit_normal(self, returns: np.ndarray) -> tuple[float, float]:
        return float(returns.mean()), float(returns.std())

