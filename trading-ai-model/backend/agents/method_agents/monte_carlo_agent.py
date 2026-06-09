"""Monte Carlo risk path agent."""

import numpy as np
import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.monte_carlo.monte_carlo_simulator import MonteCarloSimulator


class MonteCarloMethodAgent(BaseMethodAgent):
    method_name = "monte_carlo"

    def __init__(self):
        self.sim = MonteCarloSimulator()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size, shared_features=None):
        returns = ohlcv["close"].pct_change().dropna().values
        if len(returns) < 10:
            return MethodOutput(method=self.method_name, confidence=0.0, features={"paths_simulated": 0})
        paths = self.sim.run(returns, n_sims=200, horizon=min(20, len(returns)))
        prob_positive = float(np.mean(paths > 0))
        return MethodOutput(
            method=self.method_name,
            confidence=prob_positive,
            features={
                "prob_positive_path": prob_positive,
                "expected_path_pnl": float(np.mean(paths)),
                "paths_simulated": len(paths),
            },
        )

