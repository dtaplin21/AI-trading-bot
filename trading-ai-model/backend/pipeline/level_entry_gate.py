"""Level-first entry gate — price at actionable watchlist level with positive EV."""

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


def level_fast_lane_enabled() -> bool:
    return os.getenv("LEVEL_FAST_LANE", "true").lower() in ("true", "1", "yes")


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


def is_actionable_watchlist_row(row: dict[str, Any]) -> bool:
    """
    Row matches the actionable watchlist table (Price Role Entry TP% SL% R:R EV% Win% Hits).
    Requires exit optimizer fields — not every DB level qualifies.
    """
    side = str(row.get("entry_side", "")).upper()
    if side not in ("BUY", "SELL"):
        return False

    tp = _as_float(row.get("optimal_tp_pct"))
    sl = _as_float(row.get("optimal_sl_pct"))
    ev = _as_float(row.get("expected_value_pct"))
    rr = _as_float(row.get("optimal_rr"))
    if tp is None or sl is None or ev is None or rr is None:
        return False
    if tp <= 0 or sl <= 0 or ev <= 0:
        return False

    min_rr = float(os.getenv("LEVEL_GATE_MIN_RR", "1.0"))
    if rr < min_rr:
        return False

    min_win = float(os.getenv("LEVEL_GATE_MIN_WIN_RATE", "0.0"))
    win = _as_float(row.get("exit_win_rate"))
    if min_win > 0 and (win is None or win < min_win):
        return False

    return LevelSetup.from_watchlist_row("", row) is not None


class LevelEntryGate:
    """
    First gate in the live pipeline.
    Returns LevelSetup only for actionable watchlist rows at current price.
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
            row_dict = row.to_dict()
            if not is_actionable_watchlist_row(row_dict):
                continue

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

            setup = LevelSetup.from_watchlist_row(self.symbol, row_dict)
            if setup is None:
                continue

            if ev > best_ev:
                best_ev = ev
                best = setup

        if best:
            logger.info(
                "%s: actionable level | price=%.5f level=%.5f role=%s %s "
                "ev=%.3f%% rr=%.1f win=%.1f%% hits=%d",
                self.symbol,
                current_price,
                best.level_price,
                best.role,
                best.entry_side,
                best.expected_value_pct,
                best.optimal_rr,
                best.exit_win_rate * 100,
                best.touch_count,
            )
        return best
