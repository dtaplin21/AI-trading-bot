"""Momentum / acceleration agent."""

import numpy as np
import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput


class MomentumAgent(BaseMethodAgent):
    method_name = "momentum"

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        close = ohlcv["close"].values
        returns = np.diff(close) / (close[:-1] + 1e-9)
        momentum = float(np.mean(returns[-5:])) if len(returns) >= 5 else 0.0
        accel = float(np.mean(np.diff(returns[-6:]))) if len(returns) >= 6 else 0.0
        vol = float(ohlcv["volume"].iloc[-5:].mean()) if "volume" in ohlcv else 0.0
        vol_shift = min(1.0, vol / (ohlcv["volume"].mean() + 1e-9)) if "volume" in ohlcv else 0.5
        return MethodOutput(
            method=self.method_name,
            confidence=min(1.0, abs(momentum) * 50 + 0.3),
            features={
                "momentum_score": min(1.0, max(0.0, momentum * 50 + 0.5)),
                "acceleration_score": min(1.0, max(0.0, accel * 100 + 0.5)),
                "volume_shift_score": vol_shift,
            },
        )

