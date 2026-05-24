"""Probability matrix for state-to-state transitions."""

class StateTransitionMatrix:
    def __init__(self):
        self.matrix: dict[str, dict[str, float]] = {}

    def transition_prob(self, from_state: str, to_state: str) -> float:
        return self.matrix.get(from_state, {}).get(to_state, 0.0)

