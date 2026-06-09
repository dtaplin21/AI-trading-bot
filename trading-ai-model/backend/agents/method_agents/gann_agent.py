"""Gann geometry agent — research modifier only."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.geometry.gann_geometry_service import GannGeometryService


class GannAgent(BaseMethodAgent):
    method_name = "gann"

    def __init__(self):
        self.service = GannGeometryService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size, shared_features=None):
        result = self.service.analyze(ohlcv, historical_sample_size, baseline_beats_random=False)
        return MethodOutput(
            method=self.method_name,
            confidence=result.gann_confluence_score,
            features={
                "gann_angle_support": result.gann_fan_support_active,
                "gann_time_cycle_active": result.gann_time_cycle_active,
                "signal_rank_modifier": self.service.get_signal_rank_modifier(result),
                "research_only": result.research_only,
                "can_influence_signal_rank": result.can_influence_signal_rank,
            },
        )

