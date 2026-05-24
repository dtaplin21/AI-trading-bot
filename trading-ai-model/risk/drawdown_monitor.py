"""Real-time drawdown tracking."""

class DrawdownMonitor:
    def __init__(self):
        self.peak = 0.0

    def update(self, equity: float) -> float:
        self.peak = max(self.peak, equity)
        return (self.peak - equity) / self.peak * 100 if self.peak else 0.0

