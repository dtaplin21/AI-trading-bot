"""Time cycle detection: 7, 40, 49, 70-week cycles."""

CYCLES = (7, 40, 49, 490)


class BiblicalCycleService:
    def active_cycles(self, bar_index: int) -> list[int]:
        return [c for c in CYCLES if bar_index % c == 0]

