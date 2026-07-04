"""Tests for MARKET_DATA_PRIMARY parsing and Polygon demotion rules."""

from __future__ import annotations

from config.market_data_config import (
    DEFAULT_MARKET_DATA_PRIMARY,
    parse_market_data_primary,
    polygon_demoted_for_crypto,
    polygon_demoted_for_forex,
)


def test_parse_market_data_primary_default():
    assert parse_market_data_primary("") == tuple(
        DEFAULT_MARKET_DATA_PRIMARY.split(",")
    )


def test_parse_market_data_primary_strips_and_lowercases():
    assert parse_market_data_primary(" Coinbase , OANDA , Polygon ") == (
        "coinbase",
        "oanda",
        "polygon",
    )


def test_polygon_demoted_when_coinbase_before_polygon():
    primary = ("coinbase", "oanda", "polygon")
    assert polygon_demoted_for_crypto(primary) is True
    assert polygon_demoted_for_forex(primary) is True


def test_polygon_not_demoted_when_listed_first():
    primary = ("polygon", "coinbase", "oanda")
    assert polygon_demoted_for_crypto(primary) is False
    assert polygon_demoted_for_forex(primary) is False


def test_polygon_demoted_when_primary_excludes_polygon():
    primary = ("coinbase", "oanda")
    assert polygon_demoted_for_crypto(primary) is True
    assert polygon_demoted_for_forex(primary) is True
