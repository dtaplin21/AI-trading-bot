"""Market scenario generation."""

import numpy as np


class ScenarioGenerator:
    def generate(self, mu: float, sigma: float, n: int) -> np.ndarray:
        return np.random.normal(mu, sigma, n)

