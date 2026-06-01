"""Unit tests for closed learning loop."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from learning.learning_agent import LearningAgent, OUTCOMES_LOG_PATH
from learning.runtime import reset_learning_agent
from pipeline.confluence_report import ConfluenceReport, MethodVote
from pipeline.world_state_store import WorldStateStore
from risk.risk_engine import RiskEngine


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


@pytest.fixture
def learning_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNING_MIN_SAMPLES", "1")
    log_path = tmp_path / "outcomes.jsonl"
    monkeypatch.setattr("learning.learning_agent.OUTCOMES_LOG_PATH", log_path)
    reset_learning_agent()
    store = WorldStateStore()
    risk = RiskEngine()
    agent = LearningAgent(world_store=store, risk_engine=risk)
    store.store_snapshot("snap-1", _report(), signal_rank=75, predicted_p=0.65, predicted_ev=8.0)
    return agent, store, risk, log_path


def test_on_trade_closed_updates_world_and_risk(learning_setup):
    agent, store, risk, log_path = learning_setup
    agent.on_trade_closed(
        snapshot_id="snap-1",
        pnl=120.0,
        r_multiple=1.5,
        hit_target=True,
        hit_stop=False,
        mfe_ticks=18.0,
        mae_ticks=4.0,
        duration_bars=6,
        entry_price=5000.0,
        exit_price=5018.0,
        symbol="MES",
        timeframe="5m",
        signal_rank=75,
    )

    snap = store._snapshots["snap-1"]
    assert snap.outcome_label == 1
    assert snap.actual_pnl == 120.0
    assert risk._consecutive_losses == 0

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["mfe_ticks"] == 18.0
    assert row["mae_ticks"] == 4.0
    assert row["confluence_score"] == 0.55


def test_session_summary(learning_setup):
    agent, _, _, _ = learning_setup
    agent.on_trade_closed(
        snapshot_id="snap-1",
        pnl=-50.0,
        r_multiple=-1.0,
        hit_target=False,
        hit_stop=True,
        mfe_ticks=2.0,
        mae_ticks=10.0,
        duration_bars=3,
        entry_price=5000.0,
        exit_price=4990.0,
        symbol="MES",
        timeframe="5m",
        signal_rank=75,
    )
    summary = agent.session_summary()
    assert summary["trades"] == 1
    assert summary["losses"] == 1
    assert summary["avg_mae"] == 10.0


def test_trigger_retrain_resets_sample_counter(learning_setup, monkeypatch):
    from ml.training.retrain_pipeline import RetrainResult

    agent, _, _, _ = learning_setup
    result = RetrainResult()
    result.skipped = True
    monkeypatch.setattr(agent._retrain, "run", lambda **kw: result)
    agent._new_samples = 100
    agent._trigger_retrain("MES", "5m")
    assert agent._new_samples == 0


def test_force_retrain_returns_summary(learning_setup, monkeypatch):
    from ml.training.retrain_pipeline import RetrainResult

    agent, _, _, _ = learning_setup
    result = RetrainResult()
    result.model_id = "lgbm_test"
    result.n_train = 50
    result.n_holdout = 10
    monkeypatch.setattr(agent._retrain, "run", lambda **kw: result)
    summary = agent.force_retrain(requested_by="test")
    assert summary["model_id"] == "lgbm_test"
    assert summary["n_train"] == 50


def test_paper_trader_auto_close_wires_learning(monkeypatch, tmp_path):
    from learning.runtime import get_learning_agent, reset_learning_agent
    from paper_trading.paper_trader import PaperTrader, reset_paper_trader
    from paper_trading.position_book import PositionBook, get_position_book
    from pipeline.world_state_runtime import reset_world_state_store
    from risk.risk_runtime import reset_risk_engine

    reset_learning_agent()
    reset_paper_trader()
    reset_risk_engine()
    reset_world_state_store()

    monkeypatch.setenv("LEARNING_MIN_SAMPLES", "999")
    log_path = tmp_path / "outcomes.jsonl"
    monkeypatch.setattr("learning.learning_agent.OUTCOMES_LOG_PATH", log_path)

    store = __import__(
        "pipeline.world_state_runtime", fromlist=["get_world_state_store"]
    ).get_world_state_store()
    store.store_snapshot("snap-paper", _report(), signal_rank=80, predicted_p=0.7, predicted_ev=10.0)

    book = PositionBook()
    monkeypatch.setattr(
        "paper_trading.paper_trader.get_position_book", lambda: book
    )
    monkeypatch.setattr(
        "paper_trading.position_book.get_position_book", lambda: book
    )

    trader = PaperTrader(learning_agent=get_learning_agent())
    trader.execute(
        {
            "symbol": "MES",
            "action": "enter_long",
            "entry": 5000.0,
            "stop": 4990.0,
            "target": 5020.0,
            "size": 1,
            "snapshot_id": "snap-paper",
            "timeframe": "5m",
            "signal_rank": 80,
        }
    )

    closed = trader.on_bar("MES", high=5025.0, low=4995.0, close=5022.0)
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "target"
    assert closed[0]["mfe_ticks"] > 0

    snap = store._snapshots["snap-paper"]
    assert snap.outcome_label == 1
    assert log_path.exists()
