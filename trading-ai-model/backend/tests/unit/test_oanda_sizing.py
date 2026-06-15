"""Tests for OANDA USD → units conversion."""

from live.oanda_sizing import usd_to_units


def test_eurusd_converts_from_quote_currency():
    # $5 at 1.0850 ≈ 4 EUR units
    assert usd_to_units("EURUSD", 5.0, 1.0850) == 4


def test_usdjpy_uses_usd_as_base():
    assert usd_to_units("USDJPY", 5.0, 150.0) == 5


def test_usdchf_uses_usd_as_base():
    assert usd_to_units("USDCHF", 10.0, 0.88) == 10


def test_zero_entry_returns_zero():
    assert usd_to_units("EURUSD", 5.0, 0) == 0


def test_minimum_one_unit():
    assert usd_to_units("EURUSD", 0.5, 2.0) == 1
