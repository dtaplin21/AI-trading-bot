"""Tests for config/watchlist.py."""

from config.watchlist import (
    DEFAULT_SYMBOLS,
    watcher_charts_for_dashboard,
    watcher_symbols_from_env,
    watcher_timeframes_from_env,
    get_symbol_count,
    charts_grouped_by_asset_class,
)


def test_default_symbol_count():
    assert len(DEFAULT_SYMBOLS) == 23


def test_watcher_symbols_from_env(monkeypatch):
    monkeypatch.setenv(
        "WATCHER_SYMBOLS",
        "MES,ES,TSLA",
    )
    monkeypatch.delenv("CHART_WATCHLIST", raising=False)
    assert watcher_symbols_from_env() == ["MES", "ES", "TSLA"]


def test_symbol_mode_one_row_per_symbol(monkeypatch):
    monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
    monkeypatch.setenv("WATCHLIST_PRIMARY_TF", "5m")
    monkeypatch.delenv("CHART_WATCHLIST", raising=False)
    monkeypatch.delenv("WATCHER_SYMBOLS", raising=False)
    charts = watcher_charts_for_dashboard(include_session_status=False)
    assert len(charts) == 23
    assert all(c.timeframe == "5m" for c in charts)


def test_grid_mode_expands_timeframes(monkeypatch):
    monkeypatch.setenv("WATCHLIST_UI_MODE", "grid")
    monkeypatch.setenv("WATCHER_TIMEFRAMES", "1m,5m")
    monkeypatch.delenv("CHART_WATCHLIST", raising=False)
    monkeypatch.setenv("WATCHER_SYMBOLS", "MES,ES")
    charts = watcher_charts_for_dashboard(include_session_status=False)
    assert len(charts) == 4


def test_legacy_chart_watchlist_override(monkeypatch):
    monkeypatch.setenv("CHART_WATCHLIST", "MES:1m,NQ:15m")
    charts = watcher_charts_for_dashboard(include_session_status=False)
    assert len(charts) == 2
    assert charts[0].symbol == "MES"
    assert charts[0].timeframe == "1m"


def test_chart_entry_has_massive_ticker():
    charts = watcher_charts_for_dashboard(include_session_status=False)
    mes = next(c for c in charts if c.symbol == "MES")
    assert mes.massive_api_symbol == "C:MES"
    btc = next(c for c in charts if c.symbol == "BTCUSD")
    assert btc.massive_api_symbol == "X:BTCUSD"


def test_grouped_by_asset_class():
    groups = charts_grouped_by_asset_class()
    assert "Futures" in groups
    assert len(groups["Futures"]) >= 8


def test_get_symbol_count():
    counts = get_symbol_count()
    assert sum(counts.values()) == 23
