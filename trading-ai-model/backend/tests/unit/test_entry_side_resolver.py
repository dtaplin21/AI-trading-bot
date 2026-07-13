"""Tests for entry_side_resolver."""

from __future__ import annotations

from ml.features.entry_side_resolver import (
    ApproachBreakdown,
    resolve_entry_side,
)


def test_support_role_always_buy():
    side, intel = resolve_entry_side(
        "SUPPORT",
        ApproachBreakdown(from_below_total=2, from_above_total=2),
    )
    assert side == "BUY"
    assert intel["reason"] == "role_support"


def test_resistance_role_always_sell():
    side, intel = resolve_entry_side(
        "RESISTANCE",
        ApproachBreakdown(from_below_total=2, from_above_total=2),
    )
    assert side == "SELL"
    assert intel["reason"] == "role_resistance"


def test_mixed_resolves_buy_from_below_hold():
    breakdown = ApproachBreakdown(
        from_below_total=20,
        from_below_holds=14,
        from_above_total=8,
        from_above_holds=3,
    )
    side, intel = resolve_entry_side("MIXED", breakdown)
    assert side == "BUY"
    assert intel["from_below_hold_rate"] == 0.7


def test_mixed_resolves_sell_from_above_hold():
    breakdown = ApproachBreakdown(
        from_below_total=6,
        from_below_holds=2,
        from_above_total=18,
        from_above_holds=13,
    )
    side, intel = resolve_entry_side("MIXED", breakdown)
    assert side == "SELL"
    assert intel["from_above_hold_rate"] == round(13 / 18, 4)


def test_mixed_stays_either_without_enough_data():
    breakdown = ApproachBreakdown(
        from_below_total=2,
        from_below_holds=2,
        from_above_total=3,
        from_above_holds=2,
    )
    side, intel = resolve_entry_side("MIXED", breakdown)
    assert side == "EITHER"
    assert intel["reason"] == "insufficient_approach_data"
