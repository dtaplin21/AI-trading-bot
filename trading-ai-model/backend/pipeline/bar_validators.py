"""Shared bar/price validation for discovery triggers and live entry gates."""

from __future__ import annotations


def is_valid_bar_close(price: float | None) -> bool:
    """Reject zero/negative/NaN bar closes (bad forex tick data)."""
    if price is None:
        return False
    try:
        return float(price) > 0
    except (TypeError, ValueError):
        return False


def bar_touched_level(
    level_price: float,
    bar_high: float,
    bar_low: float,
    tolerance_pct: float,
) -> bool:
    """True when the bar range intersects the level zone (not close-only proximity)."""
    if level_price <= 0 or bar_high <= 0 or bar_low <= 0:
        return False
    tol = tolerance_pct / 100.0
    zone_min = level_price * (1 - tol)
    zone_max = level_price * (1 + tol)
    return bar_low <= zone_max and bar_high >= zone_min


def approach_matches_entry_side(
    entry_side: str,
    level_price: float,
    prev_close: float,
    tolerance_pct: float,
) -> bool:
    """
    BUY expects approach from below; SELL expects approach from above.
    Matches level_intelligence touch detection semantics.
    """
    if level_price <= 0 or not is_valid_bar_close(prev_close):
        return False
    side = entry_side.upper()
    if side not in ("BUY", "SELL"):
        return False
    tol = tolerance_pct / 100.0
    if side == "BUY":
        return prev_close < level_price * (1 - tol * 0.5)
    return prev_close > level_price * (1 + tol * 0.5)
