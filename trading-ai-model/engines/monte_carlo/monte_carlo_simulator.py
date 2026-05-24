"""Main Monte Carlo simulation runner."""

import numpy as np


class MonteCarloSimulator:
    def run(self, returns: np.ndarray, n_sims: int = 1000, horizon: int = 252) -> np.ndarray:
        return np.random.choice(returns, size=(n_sims, horizon)).sum(axis=1)

