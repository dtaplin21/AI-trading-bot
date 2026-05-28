"""Unit tests for trade planning router and probability gate."""

from datetime import datetime, timezone

from mcts.trade_planning_agent import TradePlanningAgent
from pipeline.confluence_report import ConfluenceReport
from pipeline.probability_gate import ProbabilityGate


def _confluence(score: float = 0.62, conflict: float = 0.15) -> ConfluenceReport:
    return ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        consensus_direction=1,
        confluence_score=score,
        conflict_score=conflict,
        news_aligned=True,
    )


def test_probability_gate_passes():
    gate = ProbabilityGate()
    result = gate.check(p_success=0.70, ev_dollars=10.0, sample_size=500, signal_rank=75)
    assert result.passed is True
    assert result.failures == []


def test_probability_gate_fails_rank():
    gate = ProbabilityGate()
    result = gate.check(p_success=0.70, ev_dollars=10.0, sample_size=500, signal_rank=40)
    assert result.passed is False
    assert any("rank" in f for f in result.failures)


def test_router_routes_to_beam_on_clear_setup():
    agent = TradePlanningAgent("MES", "5m")
    plan = agent.plan(
        confluence=_confluence(score=0.65, conflict=0.10),
        p_success=0.70,
        ev_dollars=12.0,
        sample_size=500,
        signal_rank=75,
        p_target=0.70,
        p_stop=0.20,
        entry_price=5000.0,
        stop_price=4990.0,
        target_price=5020.0,
    )
    assert agent.last_planner == "beam"
    assert plan.symbol == "MES"


def test_router_routes_to_mcts_on_low_confluence():
    agent = TradePlanningAgent("MES", "5m")
    plan = agent.plan(
        confluence=_confluence(score=0.40, conflict=0.10),
        p_success=0.70,
        ev_dollars=12.0,
        sample_size=500,
        signal_rank=75,
        p_target=0.70,
        p_stop=0.20,
        entry_price=5000.0,
        stop_price=4990.0,
        target_price=5020.0,
    )
    assert agent.last_planner == "mcts"
    assert plan.symbol == "MES"
