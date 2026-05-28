"""Reward shaping for MCTS / beam search planning."""

from __future__ import annotations


class RewardFunction:
    """Maps trade outcomes to planner reward signals."""

    def __init__(
        self,
        loss_aversion: float = 2.0,
        time_penalty_bars: int = 20,
        min_r_for_full_reward: float = 1.5,
        target_r: float = 2.0,
    ) -> None:
        self.loss_aversion = loss_aversion
        self.time_penalty_bars = time_penalty_bars
        self.min_r_for_full_reward = min_r_for_full_reward
        self.target_r = target_r

    def score(self, r_multiple: float, bars_held: int = 0) -> float:
        """Higher is better. Losses penalized by loss_aversion."""
        base = r_multiple if r_multiple >= 0 else r_multiple * self.loss_aversion
        if r_multiple >= self.min_r_for_full_reward:
            base += min(1.0, r_multiple / self.target_r) * 0.5
        if bars_held > self.time_penalty_bars:
            base -= 0.1 * (bars_held - self.time_penalty_bars)
        return round(base, 4)


class BeamSearchScorer:
    """Scores candidate action paths using RewardFunction."""

    def __init__(self, reward_fn: RewardFunction, beam_width: int = 4) -> None:
        self.reward_fn = reward_fn
        self.beam_width = beam_width

    def score_path(self, cumulative_r: float, bars_held: int = 0) -> float:
        return self.reward_fn.score(cumulative_r, bars_held)
