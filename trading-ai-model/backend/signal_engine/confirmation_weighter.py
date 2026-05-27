"""Weights layer confirmations for SignalRank."""

from config.model_weights import LayerWeights
from signal_engine.layer_scores import LayerScores


class ConfirmationWeighter:
    """Applies configured weights to normalized layer scores."""

    def __init__(self, weights: LayerWeights):
        self.weights = weights

    def weight_scores(self, scores: LayerScores) -> dict[str, float]:
        w = self.weights
        s = scores
        return {
            "candlestick": s.candlestick * w.candlestick,
            "harmonic": s.harmonic * w.harmonic,
            "elliott": s.elliott * w.elliott,
            "fibonacci": s.fibonacci * w.fibonacci,
            "number_zone": s.number_zone * w.number_zone,
            "fractal": s.fractal * w.fractal,
            "markov": s.markov * w.markov,
            "ml": s.ml * w.ml,
            "ev": s.ev * w.ev,
        }

    def explain(self, scores: LayerScores) -> dict[str, float]:
        return self.weight_scores(scores)
