"""Fibonacci retracement and extension levels."""

from dataclasses import dataclass

FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.272, 1.618, 2.0, 2.618)


@dataclass
class FibLevel:
    ratio: float
    price: float
    label: str
    distance_ticks: float


class FibonacciService:
    """Standard fib levels: retracement and extension zones."""

    def levels(self, swing_high: float, swing_low: float, direction: str = "retrace") -> list[FibLevel]:
        diff = swing_high - swing_low
        results = []
        for ratio in FIB_LEVELS:
            if direction == "retrace":
                price = swing_high - diff * ratio if swing_high > swing_low else swing_low + diff * ratio
            else:
                price = swing_high + diff * (ratio - 1) if ratio >= 1 else swing_high - diff * ratio
            results.append(
                FibLevel(
                    ratio=ratio,
                    price=price,
                    label=f"{ratio * 100:.1f}%",
                    distance_ticks=0.0,
                )
            )
        return results

    def nearest_level(self, price: float, swing_high: float, swing_low: float) -> FibLevel | None:
        levels = self.levels(swing_high, swing_low)
        if not levels:
            return None
        return min(levels, key=lambda lv: abs(lv.price - price))
