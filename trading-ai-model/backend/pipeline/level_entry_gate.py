"""Level-first entry gate — price at watchlist level with positive EV."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import pandas as pd

from config.symbols import get_symbol_or_none
from pipeline.level_setup import LevelSetup

logger = logging.getLogger(__name__)


def _gate_disabled() -> bool:
    return os.getenv("LEVEL_GATE_DISABLED", "false").lower() in ("true", "1", "yes")


def _as_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class LevelEntryGate:
    """
    First gate in the live pipeline.
    Returns LevelSetup when price is at an actionable watchlist level; else None.
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self.min_touches = int(os.getenv("LEVEL_GATE_MIN_TOUCHES", "5"))
        self.min_ev_pct = float(os.getenv("LEVEL_GATE_MIN_EV_PCT", "0.0"))
        self.min_hold_rate = float(os.getenv("LEVEL_GATE_MIN_HOLD_RATE", "0.55"))
        self.tolerance_pct = float(os.getenv("LEVEL_GATE_TOLERANCE_PCT", "0.15"))

    def check(self, current_price: float) -> Optional[LevelSetup]:
        if _gate_disabled():
            return None

        from ml.features.trade_exit_optimizer import TradeExitOptimizer

        spec = get_symbol_or_none(self.symbol)
        asset_class = spec.asset_class if spec else "equity"
        df = TradeExitOptimizer(self.symbol, asset_class).get_watchlist_with_exits()
        if df.empty:
            return None

        best: Optional[LevelSetup] = None
        best_ev = float("-inf")

        for _, row in df.iterrows():
            level_price = float(row["level_price"])
            if level_price <= 0:
                continue

            dist_pct = abs(current_price - level_price) / level_price * 100.0
            if dist_pct > self.tolerance_pct:
                continue

            touches = int(row.get("touch_count") or 0)
            if touches < self.min_touches:
                continue

            hold_rate = float(row.get("hold_rate") or 0)
            if hold_rate < self.min_hold_rate:
                continue

            ev = _as_float(row.get("expected_value_pct"))
            if ev is None or ev < self.min_ev_pct:
                continue

            setup = LevelSetup.from_watchlist_row(self.symbol, row.to_dict())
            if setup is None:
                continue

            if ev > best_ev:
                best_ev = ev
                best = setup

        if best:
            logger.info(
                "%s: level gate PASSED | price=%.5f level=%.5f ev=%.3f%% touches=%d",
                self.symbol,
                current_price,
                best.level_price,
                best.expected_value_pct,
                best.touch_count,
            )
        return best
