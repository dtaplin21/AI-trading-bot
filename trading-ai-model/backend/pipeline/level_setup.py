"""Level-driven trade setup — entry/TP/SL from DB level intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LevelSetup:
    """Actionable level trade: prices and stats from price_levels + watchlist."""

    symbol: str
    level_price: float
    entry_price: float
    target_price: float
    stop_price: float
    entry_side: str  # BUY | SELL
    hold_rate: float
    touch_count: int
    optimal_tp_pct: float
    optimal_sl_pct: float
    expected_value_pct: float
    strength_score: float = 0.0
    role: str = "UNKNOWN"
    fused_probability: float = 0.0
    method_agreement: float = 1.0

    @property
    def direction(self) -> str:
        return "long" if self.entry_side == "BUY" else "short"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "level_price": self.level_price,
            "entry_price": self.entry_price,
            "target_price": self.target_price,
            "stop_price": self.stop_price,
            "entry_side": self.entry_side,
            "hold_rate": self.hold_rate,
            "touch_count": self.touch_count,
            "optimal_tp_pct": self.optimal_tp_pct,
            "optimal_sl_pct": self.optimal_sl_pct,
            "expected_value_pct": self.expected_value_pct,
            "strength_score": self.strength_score,
            "role": self.role,
            "fused_probability": self.fused_probability,
            "method_agreement": self.method_agreement,
        }

    @classmethod
    def from_prices(
        cls,
        symbol: str,
        level_price: float,
        entry_side: str,
        optimal_tp_pct: float,
        optimal_sl_pct: float,
        hold_rate: float,
        touch_count: int,
        expected_value_pct: float,
        **kwargs: Any,
    ) -> LevelSetup:
        entry = float(level_price)
        tp_pct = float(optimal_tp_pct) / 100.0
        sl_pct = float(optimal_sl_pct) / 100.0
        side = entry_side.upper()
        if side == "BUY":
            target = entry * (1 + tp_pct)
            stop = entry * (1 - sl_pct)
        else:
            target = entry * (1 - tp_pct)
            stop = entry * (1 + sl_pct)
        return cls(
            symbol=symbol.upper(),
            level_price=entry,
            entry_price=entry,
            target_price=round(target, 6),
            stop_price=round(stop, 6),
            entry_side=side,
            hold_rate=float(hold_rate),
            touch_count=int(touch_count),
            optimal_tp_pct=float(optimal_tp_pct),
            optimal_sl_pct=float(optimal_sl_pct),
            expected_value_pct=float(expected_value_pct),
            strength_score=float(kwargs.get("strength_score", 0.0)),
            role=str(kwargs.get("role", "UNKNOWN")),
            fused_probability=float(kwargs.get("fused_probability", hold_rate)),
            method_agreement=float(kwargs.get("method_agreement", 1.0)),
        )

    @classmethod
    def from_watchlist_row(cls, symbol: str, row: dict[str, Any]) -> Optional[LevelSetup]:
        tp = row.get("optimal_tp_pct")
        sl = row.get("optimal_sl_pct")
        if tp is None or sl is None:
            return None
        side = str(row.get("entry_side", "EITHER")).upper()
        if side not in ("BUY", "SELL"):
            return None
        return cls.from_prices(
            symbol=symbol,
            level_price=float(row["level_price"]),
            entry_side=side,
            optimal_tp_pct=float(tp),
            optimal_sl_pct=float(sl),
            hold_rate=float(row.get("hold_rate") or 0),
            touch_count=int(row.get("touch_count") or 0),
            expected_value_pct=float(row.get("expected_value_pct") or 0),
            strength_score=float(row.get("strength_score") or 0),
            role=str(row.get("role", "UNKNOWN")),
        )
