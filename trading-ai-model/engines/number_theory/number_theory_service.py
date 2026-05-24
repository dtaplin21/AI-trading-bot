"""3-6-9 relationships and sacred number zones."""

class NumberTheoryService:
    def near_369_level(self, price: float, base: float) -> bool:
        ratios = [0.333, 0.666, 0.999]
        return any(abs(price / base - r) < 0.02 for r in ratios)

