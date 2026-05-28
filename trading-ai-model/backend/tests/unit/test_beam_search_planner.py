"""Unit tests for BeamSearchPlanner."""

from datetime import datetime, timezone

from mcts.beam_search_planner import BeamPath, BeamSearchPlanner
from pipeline.confluence_report import ConfluenceReport, MethodCluster


def test_beam_path_scoring():
    planner = BeamSearchPlanner()
    paths = [
        BeamPath("enter_full", 1, 0.0, 0.65, 10.0),
        BeamPath("do_nothing", 0, 0.0, 0.0, 0.0),
    ]
    confluence = ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        confluence_score=0.6,
    )
    scored = planner._score_paths(paths, confluence)
    assert scored[0].score > scored[1].score


def test_beam_search_returns_trade_plan():
    confluence = ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        consensus_direction=1,
        confluence_score=0.62,
        conflict_score=0.15,
        news_aligned=True,
        strongest_cluster=MethodCluster(
            direction=1,
            methods=["momentum", "harmonic"],
            avg_confidence=0.7,
            total_weight=0.25,
            cluster_score=0.175,
        ),
    )
    planner = BeamSearchPlanner()
    plan = planner.plan(
        confluence=confluence,
        p_target=0.65,
        p_stop=0.25,
        ev_dollars=8.0,
        entry_price=5000.0,
        stop_price=4990.0,
        target_price=5020.0,
        symbol="MES",
        timeframe="5m",
    )
    assert plan.symbol == "MES"
    assert plan.action.value in {"enter_long", "enter_short", "wait", "do_nothing"}
    assert len(planner.last_beam) <= planner.beam_width


def test_beam_search_no_ev_returns_do_nothing():
    confluence = ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        consensus_direction=1,
        confluence_score=0.62,
    )
    planner = BeamSearchPlanner()
    planner.min_ev = 1000.0
    plan = planner.plan(
        confluence=confluence,
        p_target=0.65,
        p_stop=0.25,
        ev_dollars=8.0,
        symbol="MES",
        timeframe="5m",
    )
    assert plan.action.value == "do_nothing"
