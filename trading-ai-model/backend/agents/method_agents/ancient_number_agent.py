"""Ancient number / biblical cycle agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.number_theory.biblical_cycle_service import BiblicalCycleService
from engines.number_theory.opposition_reflection_service import OppositionReflectionService


class AncientNumberAgent(BaseMethodAgent):
    method_name = "ancient_number"

    def __init__(self):
        self.cycles = BiblicalCycleService()
        self.opposition = OppositionReflectionService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        bar_index = len(ohlcv)
        price = float(ohlcv["close"].iloc[-1])
        pivot = float(ohlcv["close"].mean())
        active = self.cycles.active_cycles(bar_index)
        return MethodOutput(
            method=self.method_name,
            confidence=0.55 if active else 0.3,
            features={
                "active_cycles": active,
                "mirror_target": self.opposition.mirror_target(price, pivot),
                "number_zone": "66.6%" if abs(price / pivot - 0.666) < 0.02 else None,
            },
        )

