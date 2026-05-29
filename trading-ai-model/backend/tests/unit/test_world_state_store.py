"""Unit tests for WorldStateStore."""

from datetime import datetime, timezone

import pytest

from pipeline.confluence_report import ConfluenceReport, MethodVote
from pipeline.world_state_runtime import reset_world_state_store
from pipeline.world_state_store import WorldStateStore


def _report(
    symbol: str = "MES",
    regime: str = "trend_up",
    direction: int = 1,
    score: float = 0.55,
) -> ConfluenceReport:
    return ConfluenceReport(
        symbol=symbol,
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime=regime,
        votes=[
            MethodVote(
                method_name="momentum",
                direction=direction,
                confidence=0.7,
                weight=0.10,
                weighted_score=0.07 * direction,
                key_feature="momentum=0.70",
                is_proven=True,
            )
        ],
        bullish_count=1 if direction == 1 else 0,
        bearish_count=1 if direction == -1 else 0,
        consensus_direction=direction,
        confluence_score=score,
        total_voting=1,
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    reset_world_state_store()
    archive = tmp_path / "training_rows.jsonl"
    archive.touch()
    import pipeline.training_archive as ta

    monkeypatch.setattr(ta, "TRAINING_ROWS_PATH", archive)
    return WorldStateStore()


def test_store_and_record_outcome(store):
    report = _report()
    snap = store.store_snapshot("s1", report, signal_rank=70, predicted_p=0.62, predicted_ev=5.0)
    assert snap.outcome_label is None

    updated = store.record_outcome("s1", pnl=120.0, r_multiple=1.5, hit_target=True, hit_stop=False)
    assert updated is not None
    assert updated.outcome_label == 1
    assert "momentum" in updated.methods_that_were_right


def test_to_training_row_requires_outcome(store):
    report = _report()
    store.store_snapshot("s1", report, 70, 0.6, 3.0)
    snap = store._snapshots["s1"]
    assert snap.to_training_row() is None
    store.record_outcome("s1", pnl=-50.0, r_multiple=-1.0, hit_target=False, hit_stop=True)
    row = snap.to_training_row()
    assert row is not None
    assert row["label"] == 0
    assert row["vote_momentum"] == pytest.approx(0.7)
    assert row["signal_rank"] == 70


def test_find_similar_setups_and_p_success(store):
    base = _report(score=0.55)
    store.store_snapshot("a", base, 70, 0.6, 1.0)
    store.record_outcome("a", pnl=100.0, r_multiple=2.0, hit_target=True, hit_stop=False)

    other = _report(score=0.58)
    store.store_snapshot("b", other, 72, 0.65, 2.0)
    store.record_outcome("b", pnl=-20.0, r_multiple=-0.5, hit_target=False, hit_stop=True)

    similar = store.find_similar_setups(base, top_n=5, min_samples=1)
    assert len(similar) >= 1

    p_success, n = store.compute_historical_p_success(base, min_samples=1)
    assert n >= 1
    assert 0.0 <= p_success <= 1.0


def test_get_training_rows_filters(store):
    report = _report()
    store.store_snapshot("s1", report, signal_rank=80, predicted_p=0.7, predicted_ev=4.0)
    store.record_outcome("s1", pnl=50.0, r_multiple=1.0, hit_target=True, hit_stop=False)

    rows = store.get_training_rows(symbol="MES", min_rank=70)
    assert len(rows) == 1
    assert store.get_training_rows(symbol="ES") == []


def test_stats_and_method_accuracy(store):
    report = _report(direction=1)
    store.store_snapshot("s1", report, 70, 0.8, 1.0)
    store.record_outcome("s1", pnl=100.0, r_multiple=2.0, hit_target=True, hit_stop=False)

    stats = store.stats()
    assert stats["total_snapshots"] == 1
    assert stats["closed_trades"] == 1
    assert stats["wins"] == 1
    assert stats["brier_score"] == pytest.approx(0.04, abs=0.01)
    assert "momentum" in stats["method_accuracy"]
