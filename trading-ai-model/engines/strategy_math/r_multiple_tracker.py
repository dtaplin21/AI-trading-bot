"""R-multiple scoring per trade."""

class RMultipleTracker:
    def r_multiple(self, pnl: float, risk: float) -> float:
        return pnl / risk if risk else 0.0

