"""Unit tests for hierarchical MCTS planner and expectimax engine."""

from datetime import datetime, timezone

import pytest

from mcts.expectimax_engine import ActionNode, ExpectimaxEngine, OutcomeNode
from mcts.mcts_planner import HierarchicalMCTSPlanner, MCTSNode
from pipeline.confluence_report import ConfluenceReport


def test_outcome_node_ev():
    node = OutcomeNode("target", 0.6, 250.0, 0.6 * 250.0)
    assert node.ev == pytest.approx(150.0)


def test_action_node_compute_ev():
    action = ActionNode(
        action="enter_full",
        outcomes=[
            OutcomeNode("target", 0.6, 250.0, 150.0),
            OutcomeNode("stop", 0.3, -125.0, -37.5),
            OutcomeNode("chop", 0.1, -25.0, -2.5),
        ],
    )
    action.compute_ev(loss_aversion=2.0)
    assert action.expected_value == pytest.approx(110.0)
    assert action.risk_adjusted_ev != action.expected_value


def test_expectimax_score_actions_sorted():
    engine = ExpectimaxEngine(tick_value=1.25, loss_aversion=2.0)
    actions = engine.score_actions(p_target=0.65, p_stop=0.25)
    assert len(actions) == 4
    assert actions[0].risk_adjusted_ev >= actions[-1].risk_adjusted_ev


def test_expectimax_best_action_positive_ev():
    engine = ExpectimaxEngine(tick_value=1.25, loss_aversion=2.0)
    action, ev = engine.best_action(p_target=0.65, p_stop=0.20)
    assert action in {"enter_full", "enter_half", "wait", "do_nothing"}
    assert isinstance(ev, float)


def test_expectimax_filter_positive_ev():
    engine = ExpectimaxEngine(tick_value=1.25, loss_aversion=2.0)
    positive = engine.filter_positive_ev(p_target=0.70, p_stop=0.15)
    assert all(a.risk_adjusted_ev > 0 for a in positive)


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
    assert planner.last_audit is not None
    assert planner.last_audit["planner"] == "mcts"
    assert isinstance(planner.last_audit["best_path"], list)


def test_should_use_mcts_on_high_conflict(confluence_bullish):
    high_conflict = confluence_bullish.model_copy(update={"conflict_score": 0.45})
    assert HierarchicalMCTSPlanner.should_use_mcts(
        beam_confidence=0.9,
        confluence=high_conflict,
        signal_rank=80,
    )
