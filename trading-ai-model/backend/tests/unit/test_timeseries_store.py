"""Tests for TimeseriesStore OHLCV persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.storage.timeseries_store import (
    TimeseriesStore,
    read_bars,
    read_latest_bars,
    write_bars,
)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    return TimeseriesStore()


def test_unavailable_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch("data.storage.timeseries_store.get_settings") as mock_settings:
        mock_settings.return_value.database_url = ""
        store = TimeseriesStore()
        assert not store.available
        assert store.write("EURUSD", "1m", [{"time": "2024-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5}]) == 0
        assert store.read("EURUSD", "1m").empty


def test_write_upserts_bars(store):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    bars = [
        {
            "time": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 100,
        }
    ]

    with patch("data.storage.timeseries_store._get_connection", return_value=mock_conn):
        count = store.write("eurusd", "1m", bars)

    assert count == 1
    mock_cur.executemany.assert_called_once()
    rows = mock_cur.executemany.call_args[0][1]
    assert rows[0][1] == "EURUSD"
    mock_conn.commit.assert_called_once()
    mock_cur.close.assert_called_once()
    mock_conn.close.assert_called_once()


def test_write_bar_delegates_to_write(store):
    with patch.object(store, "write", return_value=1) as mock_write:
        store.write_bar("EURUSD", "1m", "2024-01-01", 1.0, 1.1, 0.9, 1.05, 50)
    mock_write.assert_called_once()


def test_read_returns_indexed_dataframe(store):
    raw = pd.DataFrame(
        {
            "time": ["2024-01-01T12:00:00Z"],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [100],
        }
    )
    mock_conn = MagicMock()

    with patch("data.storage.timeseries_store._get_connection", return_value=mock_conn):
        with patch("pandas.read_sql", return_value=raw) as mock_read_sql:
            df = store.read("EURUSD", "1m", start="2024-01-01", end="2024-01-02", limit=10)

    assert not df.empty
    assert df.index.name == "time"
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    mock_read_sql.assert_called_once()
    assert mock_read_sql.call_args[0][1] is mock_conn


def test_read_latest_sorts_ascending(store):
    raw = pd.DataFrame(
        {
            "time": ["2024-01-01T12:05:00Z", "2024-01-01T12:00:00Z"],
            "open": [2.0, 1.0],
            "high": [2.1, 1.1],
            "low": [1.9, 0.9],
            "close": [2.05, 1.05],
            "volume": [200, 100],
        }
    )
    mock_conn = MagicMock()

    with patch("data.storage.timeseries_store._get_connection", return_value=mock_conn):
        with patch("pandas.read_sql", return_value=raw):
            df = store.read_latest("EURUSD", "1m", n=2)

    assert df.index[0] < df.index[1]


def test_count_and_latest_timestamp(store):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
    mock_cur.fetchone.side_effect = [(42,), (ts,)]

    with patch("data.storage.timeseries_store._get_connection", return_value=mock_conn):
        assert store.count("EURUSD", "1m") == 42
        assert store.latest_timestamp("EURUSD", "1m") == ts


def test_module_helpers_delegate(store):
    with patch("data.storage.timeseries_store._store", store):
        with patch.object(store, "write", return_value=2) as mock_write:
            assert write_bars("EURUSD", "1m", []) == 2
            mock_write.assert_called_once()

        empty = pd.DataFrame()
        with patch.object(store, "read", return_value=empty) as mock_read:
            assert read_bars("EURUSD", "1m").empty
            mock_read.assert_called_once()

        with patch.object(store, "read_latest", return_value=empty) as mock_latest:
            assert read_latest_bars("EURUSD", "1m", n=100).empty
            mock_latest.assert_called_once_with("EURUSD", "1m", 100)
