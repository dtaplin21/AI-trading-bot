"""Expected Value per setup."""

class EVCalculator:
    def compute(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        return win_rate * avg_win - (1 - win_rate) * avg_loss

