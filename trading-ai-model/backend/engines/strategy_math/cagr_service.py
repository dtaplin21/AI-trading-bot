"""Compound Annual Growth Rate."""

class CAGRService:
    def compute(self, start: float, end: float, years: float) -> float:
        if start <= 0 or years <= 0:
            return 0.0
        return (end / start) ** (1 / years) - 1

