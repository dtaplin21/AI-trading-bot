"""Position correlation limits."""

class CorrelationChecker:
    def exposure(self, symbols: list[str]) -> float:
        return 0.0 if len(symbols) <= 1 else 0.5

