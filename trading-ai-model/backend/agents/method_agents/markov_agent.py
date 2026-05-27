"""Markov market state agent."""

import pandas as pd

from agents.method_agents.base_method_agent import BaseMethodAgent
from agents.schemas import MethodOutput
from engines.market_state.markov_chain_service import MarkovChainService
from engines.market_state.regime_classifier import RegimeClassifier


class MarkovAgent(BaseMethodAgent):
    method_name = "markov_state"

    def __init__(self):
        self.markov = MarkovChainService()
        self.regime = RegimeClassifier()

    def analyze(self, symbol, ohlcv, swings, historical_sample_size):
        close = ohlcv["close"]
        returns = close.pct_change().dropna()
        vol = float(returns.std()) if len(returns) else 0.01
        trend = float(returns.tail(10).mean()) if len(returns) >= 10 else 0.0
        next_state, prob = self.markov.next_state("range")
        regime = self.regime.classify(vol, trend)
        return MethodOutput(
            method=self.method_name,
            confidence=prob,
            features={
                "current_state": regime,
                "next_state": next_state,
                "markov_continuation_probability": prob if "up" in next_state or next_state == "trend_up" else 1 - prob,
                "transition_probability": prob,
            },
        )

