"""3-6-9 level analysis agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.number_theory.level_369_detector import Level369Detector
from engines.number_theory.number_theory_service import NumberTheoryService


class Level369Agent(BaseMethodAgent):
    method_name = "level_369"

    def __init__(self):
        self.detector = Level369Detector()
        self.service = NumberTheoryService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        price = float(ohlcv["close"].iloc[-1])
        anchor = float(ohlcv["close"].max())
        levels = self.detector.levels(anchor)
        near = self.service.near_369_level(price, anchor)
        nearest = min(levels, key=lambda lv: abs(lv - price))
        return MethodOutput(
            method=self.method_name,
            confidence=0.72 if near else 0.35,
            features={
                "near_369_level": near,
                "nearest_level": nearest,
                "distance_ticks": abs(price - nearest),
                "reversal_zone_active": near,
            },
        )

