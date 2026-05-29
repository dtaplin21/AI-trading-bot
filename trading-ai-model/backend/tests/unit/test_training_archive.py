"""Tests for ML training archive and WorldState DB persistence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline.confluence_report import ConfluenceReport, MethodVote
from pipeline.training_archive import append_training_row, load_training_rows, merge_training_rows
from pipeline.world_state_store import WorldStateStore


def _report() -> ConfluenceReport:
    return ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        votes=[
            MethodVote(
                method_name="momentum",
                direction=1,
                confidence=0.7,
                weight=0.10,
                weighted_score=0.07,
                key_feature="momentum=0.70",
                is_proven=True,
            )
        ],
        bullish_count=1,
        consensus_direction=1,
        confluence_score=0.55,
        total_voting=1,
    )


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.confluence: list[dict | None] = []

    async def save_snapshot(self, row: dict, confluence: dict | None = None) -> None:
        self.save_snapshot_sync(row, confluence=confluence)

    def save_snapshot_sync(self, row: dict, confluence: dict | None = None) -> None:
        from pipeline.training_archive import append_training_row

        append_training_row(row)
        self.rows.append(row)
        self.confluence.append(confluence)

    def load_training_rows(self, limit: int = 50000) -> list[dict]:
        return list(self.rows)[:limit]


def test_merge_training_rows_dedupes_by_snapshot_id():
    a = [{"snapshot_id": "s1", "label": 1}, {"snapshot_id": "s2", "label": 0}]
    b = [{"snapshot_id": "s1", "label": 0}]
    merged = merge_training_rows(a, b)
    assert len(merged) == 2
    by_id = {r["snapshot_id"]: r for r in merged}
    assert by_id["s1"]["label"] == 0


def test_append_and_load_training_rows(tmp_path, monkeypatch):
    path = tmp_path / "training_rows.jsonl"
    monkeypatch.setenv("LEARNING_TRAINING_ROWS", str(path))
    import pipeline.training_archive as archive

    monkeypatch.setattr(archive, "TRAINING_ROWS_PATH", path)

    append_training_row({"snapshot_id": "x1", "label": 1})
    rows = load_training_rows(path)
    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == "x1"


def test_world_state_persists_to_writer_and_hydrates(tmp_path, monkeypatch):
    path = tmp_path / "training_rows.jsonl"
    monkeypatch.setenv("LEARNING_TRAINING_ROWS", str(path))
    import pipeline.training_archive as archive

    monkeypatch.setattr(archive, "TRAINING_ROWS_PATH", path)

    writer = _FakeWriter()
    store = WorldStateStore(db_writer=writer)
    store.store_snapshot("snap-a", _report(), 70, 0.6, 2.0)
    store.record_outcome("snap-a", pnl=50.0, r_multiple=1.0, hit_target=True, hit_stop=False)

    assert len(writer.rows) == 1
    assert writer.rows[0]["snapshot_id"] == "snap-a"
    assert path.read_text().strip()

    store2 = WorldStateStore(db_writer=_FakeWriter())
    store2.hydrate()
    rows = store2.get_training_rows(min_rank=0, last_n_days=3650)
    assert len(rows) == 1
    assert rows[0]["label"] == 1
