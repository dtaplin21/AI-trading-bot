"""Daily loss cap enforcement."""

class DailyLossLimiter:
    def __init__(self, max_loss_pct: float):
        self.max_loss_pct = max_loss_pct

    def breached(self, daily_pnl_pct: float) -> bool:
        return daily_pnl_pct <= -self.max_loss_pct

