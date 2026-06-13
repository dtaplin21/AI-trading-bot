"""Gartley / harmonic pattern agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.geometry.harmonic_pattern_service import HarmonicPatternService


class HarmonicAgent(BaseMethodAgent):
    method_name = "harmonic"

    def __init__(self):
        self.service = HarmonicPatternService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size, shared_features=None):
        swing_tuples = [(i, p) for i, p in swings] if swings else []
        if len(swing_tuples) < 5:
            idx = list(range(len(ohlcv)))
            prices = ohlcv["close"].tolist()
            step = max(1, len(prices) // 5)
            swing_tuples = [(idx[i * step], prices[i * step]) for i in range(5)]
        swings_for_detect: list[tuple[float, float]] = [
            (float(i), float(p)) for i, p in swing_tuples
        ]
        result = self.service.detect(swings_for_detect, ohlcv, historical_sample_size)
        return MethodOutput(
            method=self.method_name,
            confidence=result.pattern_completion_score,
            features={
                "pattern": result.pattern_type,
                "xab_ratio": result.xab_ratio,
                "abc_ratio": result.abc_ratio,
                "bcd_ratio": result.bcd_ratio,
                "completion_zone": result.completion_zone,
                "production_eligible": result.production_eligible,
            },
        )

