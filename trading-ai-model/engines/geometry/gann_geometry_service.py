"""Gann geometry — research-only SignalRank modifier."""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from config.settings import get_settings


@dataclass
class GannResult:
    gann_angle_distance: float
    gann_fan_support_active: bool
    gann_time_cycle_active: bool
    price_time_square_score: float
    gann_confluence_score: float
    signal_rank_modifier: float  # +/- weight only, never standalone signal
    research_only: bool = True
    can_influence_signal_rank: bool = False


class GannGeometryService:
    """
    EXPERIMENTAL / RESEARCH MODULE

    Constraints enforced:
    - Cannot generate standalone trade signals
    - Can only modify SignalRank by +/- weight
    - Must be tagged research_only until edge proven
    - Must pass random_baseline_generator comparison
    - Minimum sample size: 300+ instances before any weight change
    """

    MIN_SAMPLE_FOR_WEIGHT = 300
    MAX_RANK_MODIFIER = 5.0

    def __init__(self, research_only: Optional[bool] = None):
        settings = get_settings()
        self.research_only = research_only if research_only is not None else settings.gann_research_only

    def analyze(
        self,
        ohlcv: pd.DataFrame,
        historical_sample_size: int = 0,
        baseline_beats_random: bool = False,
    ) -> GannResult:
        """Compute Gann geometry features. Never emits trade signals."""
        can_modify = (
            historical_sample_size >= self.MIN_SAMPLE_FOR_WEIGHT
            and baseline_beats_random
            and not self.research_only
        )

        close = ohlcv["close"]
        price_range = close.max() - close.min() or 1.0
        slope = (close.iloc[-1] - close.iloc[0]) / len(close)
        angle_distance = abs(slope / price_range)

        fan_support = angle_distance < 0.05
        time_cycle_active = len(ohlcv) % 9 == 0
        square_score = min(1.0, angle_distance * 10)

        confluence = square_score * (0.5 + 0.5 * float(fan_support))
        modifier = 0.0
        if can_modify:
            modifier = max(-self.MAX_RANK_MODIFIER, min(self.MAX_RANK_MODIFIER, (confluence - 0.5) * 10))

        return GannResult(
            gann_angle_distance=angle_distance,
            gann_fan_support_active=fan_support,
            gann_time_cycle_active=time_cycle_active,
            price_time_square_score=square_score,
            gann_confluence_score=confluence,
            signal_rank_modifier=modifier,
            research_only=self.research_only,
            can_influence_signal_rank=can_modify,
        )

    def get_signal_rank_modifier(self, result: GannResult) -> float:
        """Return rank modifier only — never a standalone signal."""
        if not result.can_influence_signal_rank:
            return 0.0
        return result.signal_rank_modifier
