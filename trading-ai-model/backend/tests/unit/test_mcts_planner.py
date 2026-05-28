"""Unit tests for hierarchical MCTS planner."""

from datetime import datetime, timezone

import pytest

from mcts.expectimax_engine import ExpectimaxEngine
from mcts.mcts_planner import HierarchicalMCTSPlanner, MCTSNode
from pipeline.confluence_report import ConfluenceReport


@pytest.fixture
def confluence_bullish() -> ConfluenceReport:
    return ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        consensus_direction=1,
        confluence_score=0.62,
        conflict_score=0.15,
        ready_for_prediction=True,
        bullish_count=4,
        total_voting=4,
    )


@pytest.fixture
def planner(monkeypatch):
    monkeypatch.setenv("MCTS_ROLLOUTS", "80")
    import importlib

    import mcts.mcts_planner as mp

    importlib.reload(mp)
    return mp.HierarchicalMCTSPlanner(symbol="MES")


def test_expectimax_expected_value():
    engine = ExpectimaxEngine(tick_value=1.25, loss_aversion=2.0)
    ev = engine.expected_value(p_target=0.6, p_stop=0.3, reward_r=2.0, risk_r=1.0)
    assert ev > 0


def test_mcts_node_ucb_and_expand():
    root = MCTSNode(state={"symbol": "MES"}, level=0)
    child = root.expand()
    assert child.level == 1
    assert child.action in {"trade", "skip"}
    assert root.visits == 0


def test_hierarchical_mcts_returns_trade_plan(planner, confluence_bullish):
    plan = planner.plan(
        confluence=confluence_bullish,
        p_target=0.65,
        p_stop=0.25,
        ev_dollars=8.0,
        entry_price=5000.0,
        stop_price=4990.0,
        target_price=5020.0,
    )
    assert plan.symbol == "MES"
    assert plan.action.value in {"enter_long", "enter_short", "wait", "do_nothing"}
    assert plan.mcts_iterations >= 80
    assert "MCTS L1-L5" in plan.plan_notes or plan.plan_notes == "MCTS L1: skip"


def test_should_use_mcts_on_high_conflict(confluence_bullish):
    high_conflict = confluence_bullish.model_copy(update={"conflict_score": 0.45})
    assert HierarchicalMCTSPlanner.should_use_mcts(
        beam_confidence=0.9,
        confluence=high_conflict,
        signal_rank=80,
    )
