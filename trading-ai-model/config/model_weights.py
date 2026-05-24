"""Layer weights for SignalRank scoring."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LayerWeights:
    candlestick: float = 0.15
    harmonic: float = 0.15
    elliott: float = 0.10
    gann: float = 0.03  # research_only modifier only
    fibonacci: float = 0.10
    number_zone: float = 0.10
    fractal: float = 0.07
    markov: float = 0.12
    ml: float = 0.10
    ev: float = 0.08


DEFAULT_LAYER_WEIGHTS = LayerWeights()


def get_layer_weights() -> LayerWeights:
    return DEFAULT_LAYER_WEIGHTS
