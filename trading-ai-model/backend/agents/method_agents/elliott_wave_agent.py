"""Probabilistic Elliott Wave agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.wave.elliott_wave_service import ElliottWaveService


class ElliottWaveAgent(BaseMethodAgent):
    method_name = "elliott_wave"

    def __init__(self):
        self.service = ElliottWaveService()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size, shared_features=None):
        result = self.service.analyze(ohlcv)
        return MethodOutput(
            method=self.method_name,
            confidence=result.wave_count_confidence,
            features={
                "elliott_state": result.wave_sequence_state,
                "wave_3_probability": result.wave_3_prob,
                "wave_5_probability": result.wave_5_prob,
                "abc_correction_probability": result.wave_a_prob + result.wave_b_prob + result.wave_c_prob,
                "impulse_candidate": result.impulse_candidate,
                "correction_candidate": result.correction_candidate,
                "can_influence_signal_rank": result.can_influence_signal_rank,
            },
        )

