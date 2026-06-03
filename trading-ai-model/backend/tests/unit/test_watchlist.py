"""
tests/unit/test_watchlist.py

Tests for config/watchlist.py and dashboard symbol alignment.

Key assertion: when WATCHER_SYMBOLS is set to the full 23-symbol string,
watcher_charts_for_dashboard() returns exactly 23 entries (symbol mode)
or 23×N entries (grid mode).

Run with:
  pytest tests/unit/test_watchlist.py -v
"""

from __future__ import annotations

import importlib

import pytest

FULL_23 = (
    "MES,ES,MNQ,NQ,CL,GC,ZB,RTY,"
    "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,"
    "BTCUSD,ETHUSD,SOLUSD,BNBUSD,XRPUSD,"
    "TSLA,NVDA,AAPL,MSFT,AMZN"
)
FULL_23_LIST = [s.strip() for s in FULL_23.split(",")]
N_SYMBOLS = len(FULL_23_LIST)
N_TIMEFRAMES = 4


def _reload_watchlist():
    import config.watchlist as wl

    importlib.reload(wl)
    return wl


def _reload_dashboard_stack():
    wl = _reload_watchlist()
    import api.services.dashboard_service as ds

    importlib.reload(ds)
    return wl, ds


class TestWatcherSymbolsFromEnv:
    def test_returns_all_23_when_env_set(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        wl = _reload_watchlist()
        symbols = wl.watcher_symbols_from_env()
        assert len(symbols) == N_SYMBOLS
        assert symbols == FULL_23_LIST

    def test_returns_defaults_when_env_empty(self, monkeypatch):
        monkeypatch.delenv("WATCHER_SYMBOLS", raising=False)
        wl = _reload_watchlist()
        symbols = wl.watcher_symbols_from_env()
        assert len(symbols) == N_SYMBOLS

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", " MES , ES , NQ ")
        wl = _reload_watchlist()
        symbols = wl.watcher_symbols_from_env()
        assert symbols == ["MES", "ES", "NQ"]

    def test_uppercase_normalisation(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "mes,nq,cl")
        wl = _reload_watchlist()
        symbols = wl.watcher_symbols_from_env()
        assert symbols == ["MES", "NQ", "CL"]


class TestWatcherChartsSymbolMode:
    def test_returns_23_rows_for_23_symbols(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHER_TIMEFRAMES", "1m,5m,15m,1h")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.setenv("WATCHLIST_PRIMARY_TF", "5m")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        assert len(charts) == N_SYMBOLS

    def test_primary_timeframe_is_5m(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "MES,NQ,BTCUSD")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.setenv("WATCHLIST_PRIMARY_TF", "5m")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        for chart in charts:
            assert chart.timeframe == "5m"

    def test_every_known_symbol_has_display_name(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        for chart in charts:
            assert chart.display_name
            assert chart.display_name != ""

    def test_asset_classes_assigned(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        by_class = {c.asset_class for c in charts}
        assert "futures" in by_class
        assert "forex" in by_class
        assert "crypto" in by_class
        assert "equity" in by_class

    def test_futures_symbols_are_futures(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "MES,ES,NQ,MNQ,CL,GC,ZB,RTY")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        for chart in charts:
            assert chart.asset_class == "futures"

    def test_forex_symbols_have_c_prefix(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        for chart in charts:
            assert chart.massive_api_symbol.startswith("C:")

    def test_crypto_symbols_have_x_prefix(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "BTCUSD,ETHUSD,SOLUSD,BNBUSD,XRPUSD")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        for chart in charts:
            assert chart.massive_api_symbol.startswith("X:")

    def test_sorted_by_asset_class_then_symbol(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        asset_classes = [c.asset_class for c in charts]
        order = {"futures": 0, "forex": 1, "crypto": 2, "equity": 3}
        for i in range(len(asset_classes) - 1):
            assert order.get(asset_classes[i], 99) <= order.get(asset_classes[i + 1], 99)

    def test_unknown_symbol_included_with_defaults(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "MES,UNKNOWNSYMBOL123")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        symbols = [c.symbol for c in charts]
        assert "UNKNOWNSYMBOL123" in symbols


class TestWatcherChartsGridMode:
    def test_grid_returns_23_x_4_rows(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHER_TIMEFRAMES", "1m,5m,15m,1h")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "grid")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        assert len(charts) == N_SYMBOLS * N_TIMEFRAMES

    def test_grid_all_timeframes_present(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", "MES,NQ")
        monkeypatch.setenv("WATCHER_TIMEFRAMES", "1m,5m,15m,1h")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "grid")
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        timeframes = {c.timeframe for c in charts}
        assert timeframes == {"1m", "5m", "15m", "1h"}


class TestLegacyChartWatchlist:
    def test_legacy_override_takes_priority(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("CHART_WATCHLIST", "MES:5m,ES:15m")
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        assert len(charts) == 2
        assert charts[0].symbol in {"MES", "ES"}

    def test_legacy_parses_symbol_colon_tf(self, monkeypatch):
        monkeypatch.setenv("CHART_WATCHLIST", "MES:5m,NQ:15m,BTCUSD:1h")
        monkeypatch.delenv("WATCHER_SYMBOLS", raising=False)
        wl = _reload_watchlist()
        charts = wl.watcher_charts_for_dashboard(include_session_status=False)
        by_sym = {c.symbol: c.timeframe for c in charts}
        assert by_sym.get("NQ") == "15m"
        assert by_sym.get("BTCUSD") == "1h"


class TestGetSymbolCount:
    def test_counts_all_asset_classes(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        wl = _reload_watchlist()
        counts = wl.get_symbol_count()
        assert counts.get("futures") == 8
        assert counts.get("forex") == 5
        assert counts.get("crypto") == 5
        assert counts.get("equity") == 5

    def test_total_matches_n_symbols(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        wl = _reload_watchlist()
        counts = wl.get_symbol_count()
        assert sum(counts.values()) == N_SYMBOLS


class TestDashboardAPI:
    def test_build_dashboard_has_23_charts(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        _, ds = _reload_dashboard_stack()
        result = ds.build_dashboard()
        assert len(result["watched_charts"]) == N_SYMBOLS

    def test_build_dashboard_includes_all_asset_classes(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.setenv("WATCHLIST_UI_MODE", "symbol")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        _, ds = _reload_dashboard_stack()
        result = ds.build_dashboard()
        classes = {c["asset_class"] for c in result["watched_charts"]}
        assert "futures" in classes
        assert "forex" in classes
        assert "crypto" in classes
        assert "equity" in classes

    def test_fallback_dashboard_never_crashes(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        _, ds = _reload_dashboard_stack()
        result = ds._fallback_dashboard()
        assert result["watched_charts"]
        assert len(result["watched_charts"]) == N_SYMBOLS

    def test_dashboard_source_field_present(self, monkeypatch):
        monkeypatch.setenv("WATCHER_SYMBOLS", FULL_23)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CHART_WATCHLIST", raising=False)
        _, ds = _reload_dashboard_stack()
        result = ds.build_dashboard()
        assert result["source"] in ("live", "fallback")


def test_full_symbol_list_length():
    assert len(FULL_23_LIST) == 23
