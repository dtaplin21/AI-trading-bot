"""Random geometric baseline for Gann/harmonic validation."""

import random
from dataclasses import dataclass

import numpy as np


@dataclass
class BaselineResult:
    random_win_rate: float
    pattern_win_rate: float
    beats_baseline: bool
    sample_size: int


class RandomBaselineGenerator:
    """Generates random geometric pattern baseline for edge comparison."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def simulate(
        self,
        pattern_outcomes: list[float],
        n_random_trials: int | None = None,
    ) -> BaselineResult:
        n = len(pattern_outcomes)
        if n == 0:
            return BaselineResult(0.0, 0.0, False, 0)

        trials = n_random_trials or n * 10
        random_outcomes = [self.rng.gauss(0, 1) for _ in range(trials)]
        pattern_wr = sum(1 for o in pattern_outcomes if o > 0) / n
        random_wr = sum(1 for o in random_outcomes if o > 0) / trials

        return BaselineResult(
            random_win_rate=random_wr,
            pattern_win_rate=pattern_wr,
            beats_baseline=pattern_wr > random_wr,
            sample_size=n,
        )

    def geometric_baseline(self, n_points: int, n_patterns: int = 1000) -> np.ndarray:
        """Random swing geometry baseline scores."""
        return np.array(
            [self.rng.uniform(0, 1) for _ in range(n_patterns)]
        )
