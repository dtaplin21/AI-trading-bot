"""Unit tests for dashboard stale DB fallback skip logic."""

from config.watchlist import WatchedChart
from api.services.dashboard_service import _should_skip_stale_db_fallback


def _chart(asset_class: str, watcher_bars: int = 0) -> WatchedChart:
    return WatchedChart(
        symbol="EURUSD",
        timeframe="5m",
        display_name="EUR/USD",
        asset_class=asset_class,
        session_type="forex_24_5",
        tick_value=0.0001,
        massive_api_symbol="C:EURUSD",
        watcher_bars_processed=watcher_bars,
    )


def test_skip_forex_when_worker_never_fed():
    assert _should_skip_stale_db_fallback(_chart("forex", 0)) is True


def test_keep_forex_when_worker_has_bars():
    assert _should_skip_stale_db_fallback(_chart("forex", 3)) is False


def test_skip_crypto_when_worker_never_fed():
    chart = _chart("crypto", 0)
    chart.symbol = "BTCUSD"
    assert _should_skip_stale_db_fallback(chart) is True


def test_keep_futures_db_fallback_when_worker_never_fed():
    chart = _chart("futures", 0)
    chart.asset_class = "futures"
    chart.symbol = "MES"
    assert _should_skip_stale_db_fallback(chart) is False
