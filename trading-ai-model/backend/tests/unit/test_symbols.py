"""Tests for config/symbols.py registry and Massive/Polygon tickers."""

import pytest

from config.symbols import (
    DEFAULT_WATCHER_SYMBOLS,
    massive_symbol,
    normalize_symbol,
    polygon_ticker_map,
    session_kind,
)
from chart_watcher.session_scheduler import SessionScheduler, WatcherMode
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def test_default_watcher_symbol_count():
    assert len(DEFAULT_WATCHER_SYMBOLS) == 23


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("MES", "C:MES"),
        ("EURUSD", "C:EURUSD"),
        ("EUR/USD", "C:EURUSD"),
        ("BTCUSD", "X:BTCUSD"),
        ("TSLA", "TSLA"),
    ],
)
def test_massive_symbol_prefixes(symbol, expected):
    assert massive_symbol(symbol) == expected


def test_polygon_ticker_map_covers_all_defaults():
    mapping = polygon_ticker_map()
    for sym in DEFAULT_WATCHER_SYMBOLS:
        assert sym in mapping
        assert mapping[sym] == massive_symbol(sym)


def test_session_kinds():
    assert session_kind("MES") == "cme_globex"
    assert session_kind("EURUSD") == "forex_24_5"
    assert session_kind("BTCUSD") == "crypto_24_7"
    assert session_kind("NVDA") == "equity_us"


def test_normalize_symbol():
    assert normalize_symbol("eur/usd") == "EURUSD"


def test_forex_closed_saturday():
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    saturday = datetime(2025, 1, 11, 15, 0, tzinfo=timezone.utc).astimezone(
        ZoneInfo("America/New_York")
    )
    assert sched.is_trading("EURUSD", at=saturday) is False


def test_equity_closed_overnight():
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    wed_night = datetime(2025, 1, 8, 6, 0, tzinfo=timezone.utc)  # 1am ET
    assert sched.is_trading("AAPL", at=wed_night) is False


def test_equity_open_premarket():
    sched = SessionScheduler(mode=WatcherMode.LIVE)
    wed_pre = datetime(2025, 1, 8, 14, 0, tzinfo=timezone.utc)  # 9am ET
    assert sched.is_trading("MSFT", at=wed_pre) is True
