"""Tests for resumable backfill checkpoint."""

import json
from pathlib import Path

from data.providers.backfill_checkpoint import CheckpointManager


def test_resume_after_chunk(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    cp = CheckpointManager(
        path,
        timeframe="1m",
        start="2025-01-01",
        end="2025-12-31",
        symbols=["MES"],
    )
    cp.load()
    assert cp.get_resume_date("MES") == "2025-01-01"
    cp.mark_chunk_done("MES", "2025-01-30", 1000)
    assert cp.get_resume_date("MES") == "2025-01-31"
    cp.mark_symbol_done("MES")
    assert cp.get_resume_date("MES") is None
    assert cp.is_done("MES")


def test_reset_futures_keeps_forex(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "timeframe": "1m",
                "start": "2025-01-01",
                "end": "2025-12-31",
                "symbols": {
                    "MES": {
                        "status": "done",
                        "last_date": "2025-12-31",
                        "bars_saved": 0,
                        "chunks_done": 5,
                    },
                    "EURUSD": {
                        "status": "in_progress",
                        "last_date": "2025-03-01",
                        "bars_saved": 58910,
                        "chunks_done": 2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    cp = CheckpointManager(
        path, timeframe="1m", start="2025-01-01", end="2025-12-31", symbols=["MES", "EURUSD"]
    )
    cp.load()
    reset = cp.reset_futures_for_rebackfill(["MES"], only_zero_bars=True)
    assert reset == ["MES"]
    assert cp._data["symbols"]["MES"]["status"] == "pending"
    assert cp._data["symbols"]["EURUSD"]["bars_saved"] == 58910
    assert cp._data["symbols"]["EURUSD"]["status"] == "in_progress"


def test_load_mismatch_restarts_fresh(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "timeframe": "5m",
                "start": "2024-01-01",
                "end": "2024-12-31",
                "symbols": {"MES": {"status": "done"}},
            }
        ),
        encoding="utf-8",
    )
    cp = CheckpointManager(
        path,
        timeframe="1m",
        start="2025-01-01",
        end="2025-12-31",
        symbols=["MES"],
    )
    cp.load()
    assert not cp.is_done("MES")
