"""Sharpe and Sortino ratios."""

import numpy as np


class SharpeCalculator:
    def sharpe(self, returns: np.ndarray, rf: float = 0.0) -> float:
        std = returns.std()
        return 0.0 if std == 0 else (returns.mean() - rf) / std * np.sqrt(252)

