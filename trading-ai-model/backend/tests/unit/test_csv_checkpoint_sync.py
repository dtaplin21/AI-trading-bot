"""Tests for CSV → checkpoint sync."""

import json
from pathlib import Path

from data.providers.backfill_checkpoint import CheckpointManager
from data.providers.csv_checkpoint_sync import inspect_ohlcv_csv, sync_checkpoint_from_csv


def test_inspect_ohlcv_csv(tmp_path: Path):
    path = tmp_path / "EURUSD_1m.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-01T00:00:00Z,1,1,1,1,0\n"
        "2025-12-31T23:59:00Z,2,2,2,2,0\n",
        encoding="utf-8",
    )
    stats = inspect_ohlcv_csv(path)
    assert stats is not None
    assert stats.rows == 2
    assert stats.first_date == "2025-01-01"
    assert stats.last_date == "2025-12-31"


def test_sync_marks_done_when_csv_covers_job(tmp_path: Path):
    csv_dir = tmp_path / "ohlcv"
    csv_dir.mkdir()
    (csv_dir / "BTCUSD_1m.csv").write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-01T00:00:00Z,1,1,1,1,0\n"
        "2025-12-31T00:00:00Z,2,2,2,2,0\n",
        encoding="utf-8",
    )
    cp_path = tmp_path / "checkpoint.json"
    cp = CheckpointManager(
        cp_path,
        timeframe="1m",
        start="2025-01-01",
        end="2025-12-31",
        symbols=["BTCUSD", "MES"],
    )
    cp.load()
    sync_checkpoint_from_csv(cp, csv_dir, timeframe="1m", symbols=["BTCUSD", "MES"])
    assert cp._data["symbols"]["BTCUSD"]["status"] == "done"
    assert cp._data["symbols"]["BTCUSD"]["bars_saved"] == 2
    assert cp._data["symbols"]["MES"]["status"] == "pending"


def test_sync_partial_start_gap(tmp_path: Path):
    csv_dir = tmp_path / "ohlcv"
    csv_dir.mkdir()
    (csv_dir / "GBPUSD_1m.csv").write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-05-01T00:00:00Z,1,1,1,1,0\n"
        "2025-12-31T00:00:00Z,2,2,2,2,0\n",
        encoding="utf-8",
    )
    cp = CheckpointManager(
        tmp_path / "cp.json",
        timeframe="1m",
        start="2025-01-01",
        end="2025-12-31",
        symbols=["GBPUSD"],
    )
    cp.load()
    sync_checkpoint_from_csv(cp, csv_dir, timeframe="1m", symbols=["GBPUSD"])
    entry = cp._data["symbols"]["GBPUSD"]
    assert entry["status"] == "in_progress"
    assert entry["last_date"] is None
    assert entry["bars_saved"] == 2
    assert cp.get_resume_date("GBPUSD") == "2025-01-01"
