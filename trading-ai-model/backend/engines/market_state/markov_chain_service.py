"""Hidden Markov Models for state transitions."""

STATES = ("trend_up", "trend_down", "range", "breakout", "reversal", "chop")


class MarkovChainService:
    def next_state(self, current: str) -> tuple[str, float]:
        return "range", 0.5

