"""Layer score inputs for SignalRank computation."""

from dataclasses import dataclass


@dataclass
class LayerScores:
    candlestick: float = 0.0
    harmonic: float = 0.0
    elliott: float = 0.0
    gann_modifier: float = 0.0
    fibonacci: float = 0.0
    number_zone: float = 0.0
    fractal: float = 0.0
    markov: float = 0.0
    ml: float = 0.0
    ev: float = 0.0
