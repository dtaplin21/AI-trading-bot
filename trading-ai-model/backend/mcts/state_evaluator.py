"""mcts/state_evaluator.py"""
from __future__ import annotations


class StateEvaluator:
    """
    Evaluates a trade planning state and returns a value 0-1.
    Higher = better position to be in.

    Used by MCTS to estimate leaf node values during rollout.
    """

    def evaluate(self, state: dict) -> float:
        """
        Score a trade state based on:
          - Current P&L relative to risk
          - Time remaining in trade
          - Current reversal probability
          - Distance to target vs stop
        """
        score = 0.5

        risk = state.get("risk_amount", 1)
        pnl = state.get("unrealized_pnl", 0)
        r_mult = pnl / (risk + 1e-10)
        score += min(0.3, max(-0.3, r_mult * 0.1))

        bars_elapsed = state.get("bars_elapsed", 0)
        max_bars = state.get("max_bars", 20)
        time_pct = bars_elapsed / (max_bars + 1e-10)
        score -= time_pct * 0.1

        rev_prob = state.get("reversal_prob", 0.5)
        score += (rev_prob - 0.5) * 0.2

        d_target = state.get("dist_to_target", 1)
        d_stop = state.get("dist_to_stop", 1)
        if d_stop > 0:
            rr = d_target / d_stop
            score += min(0.1, (rr - 1) * 0.05)

        return float(max(0.0, min(1.0, score)))
