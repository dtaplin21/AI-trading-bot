"""
mcts/expectimax_engine.py

Probability-weighted outcome evaluator for MCTS rollouts.
Computes expected value across target / stop / chop branches.
"""

from __future__ import annotations


class ExpectimaxEngine:
    """Expectimax-style probability weighting for trade outcomes."""

    def __init__(self, tick_value: float = 1.25, loss_aversion: float = 2.0) -> None:
        self.tick_value = tick_value
        self.loss_aversion = loss_aversion

    def expected_value(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        size_mult: float = 1.0,
    ) -> float:
        p_target = max(0.0, min(1.0, p_target))
        p_stop = max(0.0, min(1.0, p_stop))
        p_chop = max(0.0, 1.0 - p_target - p_stop)

        win = reward_r * size_mult * self.tick_value * 10
        loss = risk_r * size_mult * self.tick_value * 10 * self.loss_aversion
        chop = 0.15 * size_mult * self.tick_value * 10

        return round(p_target * win - p_stop * loss - p_chop * chop, 4)

    def sample_outcome(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        size_mult: float = 1.0,
        roll: float | None = None,
    ) -> float:
        """Single stochastic outcome for rollout simulation."""
        import random

        r = random.random() if roll is None else roll
        p_target = max(0.0, min(1.0, p_target))
        p_stop = max(0.0, min(1.0, p_stop))

        if r < p_target:
            return reward_r * size_mult * self.tick_value * 10
        if r < p_target + p_stop:
            return -(risk_r * size_mult * self.tick_value * 10) * self.loss_aversion
        return -(0.15 * size_mult * self.tick_value * 10)
