"""Tests for TimescaleStore graceful fallback."""

import pandas as pd

from data.storage.timescale_store import TimescaleStore


def test_unavailable_without_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    from config.settings import get_settings

    get_settings.cache_clear()
    store = TimescaleStore()
    assert store.available is False
    df = store.load_ohlcv("MES")
    assert df.empty


def test_upsert_noop_when_unavailable():
    store = TimescaleStore(database_url="")
    assert store.available is False
    ohlcv = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [100]},
        index=pd.date_range("2026-01-01", periods=1, freq="5min"),
    )
    assert store.upsert_ohlcv("MES", "5m", ohlcv) == 0


def test_load_ohlcv_range_empty_when_unavailable():
    from datetime import datetime, timezone

    store = TimescaleStore(database_url="")
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    df = store.load_ohlcv_range("MES", "1m", start, end)
    assert df.empty
