"""Dynamic position sizing."""

from __future__ import annotations


class PositionSizer:
    def size(
        self,
        account: float,
        risk_pct: float,
        stop_distance: float,
        correlation_factor: float = 1.0,
    ) -> int:
        if stop_distance <= 0:
            return 0
        raw = int((account * risk_pct / 100) / stop_distance)
        factor = max(0.0, min(1.0, correlation_factor))
        return max(0, int(raw * factor))

