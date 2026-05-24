"""Kelly Criterion and risk of ruin."""

class RiskOfRuinCalculator:
    def kelly_fraction(self, win_rate: float, win_loss_ratio: float) -> float:
        if win_loss_ratio <= 0:
            return 0.0
        return max(0.0, win_rate - (1 - win_rate) / win_loss_ratio)

