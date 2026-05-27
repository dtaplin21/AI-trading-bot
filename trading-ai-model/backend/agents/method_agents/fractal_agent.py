"""Fractal / chaos analysis agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.wave.fractal_service import FractalService


class FractalAgent(BaseMethodAgent):
    method_name = "fractal"

    def __init__(self):
        self.service = FractalService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        result = self.service.detect(ohlcv)
        return MethodOutput(
            method=self.method_name,
            confidence=0.6 if result.get("fractal_down") or result.get("fractal_up") else 0.3,
            features={
                "fractal_down_confirmed": result.get("fractal_down", False),
                "fractal_up_confirmed": result.get("fractal_up", False),
            },
        )

