"""Classifies trending / ranging / volatile / quiet."""

class RegimeClassifier:
    def classify(self, volatility: float, trend_strength: float) -> str:
        if volatility > 0.02:
            return "volatile"
        if abs(trend_strength) > 0.01:
            return "trending"
        return "ranging"

