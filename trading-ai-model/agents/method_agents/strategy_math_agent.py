"""Strategy math — EV, risk of ruin, Sharpe."""

import numpy as np
import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.strategy_math.ev_calculator import EVCalculator
from engines.strategy_math.risk_of_ruin_calculator import RiskOfRuinCalculator
from engines.strategy_math.sharpe_calculator import SharpeCalculator


class StrategyMathAgent(BaseMethodAgent):
    method_name = "strategy_math"

    def __init__(self):
        self.ev = EVCalculator()
        self.ror = RiskOfRuinCalculator()
        self.sharpe = SharpeCalculator()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        returns = ohlcv["close"].pct_change().dropna().values
        win_rate = float(np.mean(returns > 0)) if len(returns) else 0.5
        avg_win = float(returns[returns > 0].mean()) if np.any(returns > 0) else 0.01
        avg_loss = float(abs(returns[returns < 0].mean())) if np.any(returns < 0) else 0.01
        ev = self.ev.compute(win_rate, avg_win * 1000, avg_loss * 1000)
        ror = self.ror.kelly_fraction(win_rate, avg_win / (avg_loss + 1e-9))
        return MethodOutput(
            method=self.method_name,
            confidence=min(1.0, max(0.0, ev / 20)),
            features={
                "strategy_ev": ev,
                "risk_of_ruin": max(0.0, 1 - ror),
                "win_rate": win_rate,
                "sharpe": self.sharpe.sharpe(returns) if len(returns) > 5 else 0.0,
                "historical_sample_size": historical_sample_size,
            },
        )

