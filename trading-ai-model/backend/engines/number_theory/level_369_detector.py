"""Detects 3/6/9-derived price levels."""

class Level369Detector:
    def levels(self, anchor: float) -> list[float]:
        return [anchor * r for r in (0.333, 0.666, 0.999)]

