"""Candlestick / Homma analysis agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.price_action.candlestick_psychology_service import CandlestickPsychologyService
from engines.price_action.wick_analysis_service import WickAnalysisService


class CandlestickAgent(BaseMethodAgent):
    method_name = "candlestick"

    def __init__(self):
        self.candles = CandlestickPsychologyService()
        self.wicks = WickAnalysisService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size, shared_features=None):
        psych = self.candles.analyze(ohlcv)
        wick = self.wicks.analyze(ohlcv)
        bullish_rejection = psych.lower_wick_ratio > 0.5 and psych.rejection_score > 0.4
        return MethodOutput(
            method=self.method_name,
            confidence=psych.rejection_score,
            features={
                "pattern": psych.pattern_name,
                "bullish_rejection_candle": bullish_rejection,
                "wick_rejection_score": psych.rejection_score,
                "body_to_range_ratio": psych.body_to_range_ratio,
                "confirmation": psych.rejection_score > 0.5,
                **wick,
            },
        )

