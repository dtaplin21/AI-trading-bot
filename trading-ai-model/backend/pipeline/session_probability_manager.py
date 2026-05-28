"""Session-level probability queue for ranked setups."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SessionSetup(BaseModel):
    setup_id: str
    symbol: str
    timeframe: str
    direction: str
    timestamp_scored: datetime
    p_success: float
    ev_dollars: float
    signal_rank: int
    sample_size: int = 0
    methods_agreed: list[str] = Field(default_factory=list)
    methods_disagreed: list[str] = Field(default_factory=list)
    methods_excluded: list[str] = Field(default_factory=list)
    confluence_score: float = 0.0
    regime: str = "chop"
    news_aligned: bool = True
    conflict_score: float = 0.0


class SessionProbabilityManager:
    """Tracks scored setups for the current session."""

    def __init__(
        self,
        watched_symbols: list[str] | None = None,
        watched_timeframes: list[str] | None = None,
    ) -> None:
        self.watched_symbols = watched_symbols or []
        self.watched_timeframes = watched_timeframes or []
        self._setups: list[SessionSetup] = []
        self._outcomes: dict[str, dict] = {}

    def add_setup(self, setup: SessionSetup) -> None:
        if self.watched_symbols and setup.symbol not in self.watched_symbols:
            return
        if self.watched_timeframes and setup.timeframe not in self.watched_timeframes:
            return
        self._setups.append(setup)
        self._setups.sort(key=lambda s: (-s.p_success, -s.signal_rank))

    def record_outcome(self, setup_id: str, pnl: float, r_multiple: float) -> None:
        self._outcomes[setup_id] = {
            "pnl": pnl,
            "r_multiple": r_multiple,
        }

    def get_top_setups(self, n: int = 10, symbol: Optional[str] = None) -> list[SessionSetup]:
        items = self._setups
        if symbol:
            items = [s for s in items if s.symbol == symbol]
        return items[:n]

    def clear(self) -> None:
        self._setups.clear()

    def count(self) -> int:
        return len(self._setups)
