"""ml/evaluation/backtest_evaluator.py"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


class BacktestEvaluator:
    """
    Runs a simplified backtest on model predictions vs actual outcomes.
    Used to validate model quality before promoting to live.
    """

    def __init__(
        self,
        initial_capital: float = 10_000,
        risk_per_trade: float = 0.01,
        min_probability: float = 0.62,
        commission: float = 0.0002,
    ):
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.min_probability = min_probability
        self.commission = commission

    def run(
        self,
        predictions: pd.Series,
        outcomes: pd.Series,
        prices: pd.Series,
    ) -> dict:
        """
        Simulate trades based on predictions and actual outcomes.

        Returns dict with equity_curve, metrics, trade_log.
        """
        capital = self.initial_capital
        trades: list[dict] = []
        equity = [capital]

        for i in range(len(predictions)):
            pred = float(predictions.iloc[i])
            outcome = float(outcomes.iloc[i])
            price = float(prices.iloc[i])

            if pred < self.min_probability:
                equity.append(capital)
                continue

            risk_amount = capital * self.risk_per_trade
            stop_pct = 0.005
            size = risk_amount / (price * stop_pct)

            if outcome == 1:
                pnl = size * price * 0.003
            else:
                pnl = -size * price * stop_pct

            commission = size * price * self.commission * 2
            net_pnl = pnl - commission

            capital += net_pnl
            equity.append(capital)
            trades.append(
                {
                    "i": i,
                    "pred": round(pred, 4),
                    "outcome": int(outcome),
                    "pnl": round(net_pnl, 2),
                    "capital": round(capital, 2),
                }
            )

        equity_series = pd.Series(equity)
        returns = equity_series.pct_change().dropna()

        n_trades = len(trades)
        n_wins = sum(1 for t in trades if t["pnl"] > 0)
        total_pnl = capital - self.initial_capital

        metrics = {
            "total_return_pct": round((capital / self.initial_capital - 1) * 100, 2),
            "n_trades": n_trades,
            "win_rate": round(n_wins / n_trades, 4) if n_trades else 0,
            "profit_factor": self._profit_factor(trades),
            "max_drawdown_pct": round(self._max_drawdown(equity_series) * 100, 2),
            "sharpe": round(self._sharpe(returns), 3),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(
                float(np.mean([t["pnl"] for t in trades if t["pnl"] > 0] or [0])), 2
            ),
            "avg_loss": round(
                float(np.mean([t["pnl"] for t in trades if t["pnl"] <= 0] or [0])), 2
            ),
        }

        return {
            "metrics": metrics,
            "equity_curve": equity,
            "trade_log": trades,
        }

    def _profit_factor(self, trades: list[dict]) -> float:
        gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
        return round(gross_win / (gross_loss + 1e-10), 3)

    def _max_drawdown(self, equity: pd.Series) -> float:
        peak = equity.expanding().max()
        dd = (equity - peak) / (peak + 1e-10)
        return float(abs(dd.min()))

    def _sharpe(self, returns: pd.Series, periods: int = 252) -> float:
        if returns.std() == 0:
            return 0.0
        return float(returns.mean() / returns.std() * np.sqrt(periods))
