"""Fibonacci / spiral analysis agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.geometry.fibonacci_service import FibonacciService
from engines.geometry.sacred_geometry_service import SacredGeometryService


class FibonacciAgent(BaseMethodAgent):
    method_name = "fibonacci_spiral"

    def __init__(self):
        self.fib = FibonacciService()
        self.spiral = SacredGeometryService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        high, low = float(ohlcv["high"].max()), float(ohlcv["low"].min())
        price = float(ohlcv["close"].iloc[-1])
        nearest = self.fib.nearest_level(price, high, low)
        near_618 = nearest and abs(nearest.ratio - 0.618) < 0.05
        return MethodOutput(
            method=self.method_name,
            confidence=0.72 if near_618 else 0.45,
            features={
                "nearest_level": nearest.label if nearest else None,
                "distance_ticks": abs(price - nearest.price) if nearest else None,
                "near_618_fib": near_618,
                "reversal_zone_active": near_618,
                "golden_zone": self.spiral.golden_zone(price),
            },
        )

