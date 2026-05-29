"""Tests for compact MCTS / beam planner audit persistence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mcts.beam_search_planner import BeamPath
from mcts.mcts_planner import MCTSNode
from mcts.planner_audit import build_beam_audit, build_mcts_audit, envelope_audit
from pipeline.confluence_report import ConfluenceReport
from pipeline.planner_audit_service import DEEP_PLANNERS, persist_planner_audit
from pipeline.schemas import TradeAction, TradePlan


def _confluence() -> ConfluenceReport:
    return ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        consensus_direction=1,
        confluence_score=0.62,
        conflict_score=0.15,
        news_aligned=True,
        bullish_count=4,
        total_voting=4,
    )


def _plan() -> TradePlan:
    return TradePlan(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        action=TradeAction.ENTER_LONG,
        plan_ev=12.5,
        plan_confidence=0.65,
        plan_notes="test",
    )


def test_build_mcts_audit_summarizes_path():
    root = MCTSNode(state={"symbol": "MES"}, level=0)
    trade = MCTSNode(state={"L1": "trade"}, parent=root, action="trade", level=1)
    trade.visits = 10
    trade.value_sum = 5.0
    skip = MCTSNode(state={"L1": "skip"}, parent=root, action="skip", level=1)
    skip.visits = 2
    skip.value_sum = -1.0
    root.children = [trade, skip]

    path = [root, trade]
    audit = build_mcts_audit(path[0], path, rollouts=100, exploration_c=1.414)

    assert audit["planner"] == "mcts"
    assert audit["rollouts"] == 100
    assert audit["best_path"] == ["trade"]
    actions = {a["action"] for a in audit["alternative_paths"]}
    assert actions == {"trade", "skip"}


def test_build_beam_audit_lists_candidates():
    beam = [
        BeamPath(
            action="enter_long",
            direction=1,
            score=0.8,
            p_success=0.65,
            ev_dollars=10.0,
            entry_condition="now",
            stop_condition="normal",
            target_condition="2R",
            notes="top",
        ),
        BeamPath(
            action="wait",
            direction=0,
            score=0.3,
            p_success=0.5,
            ev_dollars=1.0,
            entry_condition="next",
            stop_condition="tight",
            target_condition="1R",
            notes="alt",
        ),
    ]
    audit = build_beam_audit(beam, beam_width=2)

    assert audit["planner"] == "beam"
    assert audit["best_path"] == ["enter_long"]
    assert len(audit["alternative_paths"]) == 2
    assert audit["search_stats"]["top_score"] == pytest.approx(0.8)


def test_envelope_audit_merges_context():
    audit = build_beam_audit([], beam_width=3)
    record = envelope_audit(
        audit,
        snapshot_id="snap-1",
        symbol="MES",
        timeframe="5m",
        confluence=_confluence(),
        plan=_plan(),
        p_success=0.65,
        ev_dollars=10.0,
        signal_rank=72,
    )

    assert record["snapshot_id"] == "snap-1"
    assert record["planner"] == "beam"
    assert record["full_audit"]["signal_rank"] == 72
    assert record["full_audit"]["plan_notes"] == "test"


class _FakeStore:
    available = True

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_planner_audit(self, record: dict) -> None:
        self.rows.append(record)


def test_persist_planner_audit_skips_non_deep_planners(tmp_path, monkeypatch):
    path = tmp_path / "planner_audits.jsonl"
    monkeypatch.setenv("PLANNER_AUDIT_LOG", str(path))
    import pipeline.planner_audit_service as svc

    monkeypatch.setattr(svc, "PLANNER_AUDIT_LOG", path)

    store = _FakeStore()
    persist_planner_audit(
        {"planner": "expectimax"},
        snapshot_id="s1",
        symbol="MES",
        timeframe="5m",
        confluence=_confluence(),
        plan=_plan(),
        p_success=0.6,
        ev_dollars=5.0,
        signal_rank=70,
        store=store,
    )
    assert store.rows == []
    assert not path.exists()


def test_persist_planner_audit_writes_jsonl_and_db(tmp_path, monkeypatch):
    path = tmp_path / "planner_audits.jsonl"
    monkeypatch.setenv("PLANNER_AUDIT_LOG", str(path))
    import pipeline.planner_audit_service as svc

    monkeypatch.setattr(svc, "PLANNER_AUDIT_LOG", path)

    audit = build_mcts_audit(
        MCTSNode(state={}, level=0),
        [MCTSNode(state={}, level=0)],
        rollouts=50,
        exploration_c=1.0,
    )
    assert audit["planner"] in DEEP_PLANNERS

    store = _FakeStore()
    persist_planner_audit(
        audit,
        snapshot_id="snap-mcts",
        symbol="MES",
        timeframe="5m",
        confluence=_confluence(),
        plan=_plan(),
        p_success=0.65,
        ev_dollars=10.0,
        signal_rank=72,
        store=store,
    )

    assert len(store.rows) == 1
    assert store.rows[0]["planner"] == "mcts"
    assert path.read_text().strip()
