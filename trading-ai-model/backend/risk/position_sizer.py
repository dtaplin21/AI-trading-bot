"""Dynamic position sizing."""

class PositionSizer:
    def size(self, account: float, risk_pct: float, stop_distance: float) -> int:
        if stop_distance <= 0:
            return 0
        return int((account * risk_pct / 100) / stop_distance)

