"""Win/loss streak analysis."""

class StreakProbabilityService:
    def streak_prob(self, win_rate: float, streak_len: int) -> float:
        return win_rate ** streak_len

