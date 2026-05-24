"""Aggregates geometry signals into one confluence score."""

class GeometryConfluenceScorer:
    def score(self, fib: float, harmonic: float, gann: float) -> float:
        return (fib * 0.4 + harmonic * 0.45 + gann * 0.15)

