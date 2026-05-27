"""Balance line / support-resistance equilibrium agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput


class BalanceLineAgent(BaseMethodAgent):
    method_name = "balance_line"

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        close = ohlcv["close"]
        mid = (float(close.max()) + float(close.min())) / 2
        price = float(close.iloc[-1])
        at_balance = abs(price - mid) / mid < 0.002 if mid else False
        return MethodOutput(
            method=self.method_name,
            confidence=0.65 if at_balance else 0.4,
            features={
                "balance_line": mid,
                "at_balance_line": at_balance,
                "above_balance": price > mid,
            },
        )

