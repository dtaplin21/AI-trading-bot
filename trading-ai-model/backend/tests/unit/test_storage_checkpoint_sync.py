"""Tests for CSV + DB → checkpoint sync."""

from pathlib import Path
from unittest.mock import MagicMock

from data.providers.backfill_checkpoint import CheckpointManager
from data.providers.storage_checkpoint_sync import (
    StorageStats,
    merge_storage_stats,
    sync_checkpoint_from_storage,
)


def test_merge_storage_stats_widest_range():
    csv = StorageStats(rows=100, first_date="2025-01-01", last_date="2025-06-01", source="csv")
    db = StorageStats(rows=200, first_date="2025-01-01", last_date="2025-12-31", source="db")
    merged = merge_storage_stats(csv, db)
    assert merged is not None
    assert merged.rows == 200
    assert merged.last_date == "2025-12-31"
    assert merged.source == "csv+db"


def test_sync_from_db_marks_done(tmp_path: Path):
    cp = CheckpointManager(
        tmp_path / "cp.json",
        timeframe="1m",
        start="2025-01-01",
        end="2025-12-31",
        symbols=["EURUSD"],
    )
    cp.load()

    store = MagicMock()
    store.available = True
    store.ohlcv_storage_stats.return_value = (58910, None, None)
    from datetime import datetime, timezone

    store.ohlcv_storage_stats.return_value = (
        58910,
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 12, 31, tzinfo=timezone.utc),
    )

    sync_checkpoint_from_storage(
        cp,
        tmp_path / "ohlcv",
        timeframe="1m",
        symbols=["EURUSD"],
        store=store,
        use_csv=False,
        use_db=True,
    )
    entry = cp._data["symbols"]["EURUSD"]
    assert entry["status"] == "done"
    assert entry["bars_saved"] == 58910
