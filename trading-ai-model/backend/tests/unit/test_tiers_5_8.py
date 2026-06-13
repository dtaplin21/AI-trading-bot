"""Tests for Tiers 5-8 implementations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from engines.symbol_intelligence.session_analyzer import SessionAnalyzer
from live.live_position_monitor import LivePosition, LivePositionMonitor
from live.order_router import OrderRouter
from mcts.policy_network import PolicyNetwork
from mcts.state_evaluator import StateEvaluator
from mcts.tree_node import TreeNode
from ml.evaluation.backtest_evaluator import BacktestEvaluator
from validation.edge_validator import EdgeValidator
from validation.walk_forward_tester import WalkForwardTester


def test_backtest_evaluator_runs_trades():
    preds = pd.Series([0.7, 0.5, 0.8, 0.65])
    outcomes = pd.Series([1, 0, 1, 0])
    prices = pd.Series([100.0, 101.0, 102.0, 103.0])
    result = BacktestEvaluator().run(preds, outcomes, prices)
    assert result["metrics"]["n_trades"] == 3
    assert len(result["equity_curve"]) == 5


def test_walk_forward_tester_with_mock_model():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(300, 4)), columns=list("abcd"))
    y = pd.Series((rng.random(300) > 0.5).astype(int))

    class MockModel:
        def fit(self, X_train, y_train):
            return self

        def predict(self, X_test):
            return np.full(len(X_test), 0.6)

    out = WalkForwardTester(n_splits=3).test(X, y, MockModel())
    assert "mean_auc" in out
    assert len(out.get("folds", [])) >= 1


def test_edge_validator_passes_strong_edge():
    preds = [0.7] * 40
    outcomes = [1] * 30 + [0] * 10
    out = EdgeValidator(min_edge_pct=0.05).validate(preds, outcomes, base_rate=0.5)
    assert out["passed"] is True
    assert out["hit_rate"] == 0.75


def test_tree_node_ucb_and_update():
    root = TreeNode({"pnl": 0})
    child = TreeNode({"pnl": 1}, action="exit", parent=root, prior=0.5)
    root.children.append(child)
    child.update(0.8)
    assert child.q_value == 0.8
    assert root.best_child() is child


def test_state_evaluator_bounded():
    score = StateEvaluator().evaluate(
        {
            "unrealized_pnl": 100,
            "risk_amount": 50,
            "bars_elapsed": 2,
            "max_bars": 20,
            "reversal_prob": 0.7,
            "dist_to_target": 2,
            "dist_to_stop": 1,
        }
    )
    assert 0.0 <= score <= 1.0


def test_policy_network_sums_to_one():
    priors = PolicyNetwork().get_priors({"reversal_prob": 0.65, "bars_elapsed": 3, "max_bars": 20})
    assert abs(sum(priors.values()) - 1.0) < 0.01


def test_session_analyzer_overlap():
    sa = SessionAnalyzer()
    dt = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)
    assert sa.get_current_session(dt) == "OVERLAP"
    assert sa.is_symbol_active("MES", dt) is True


def test_position_monitor_tp_hit():
    monitor = LivePositionMonitor()
    monitor.configure(paper_mode=True)
    monitor.register(
        LivePosition(
            trade_id="t1",
            symbol="EURUSD",
            side="LONG",
            entry_price=1.0843,
            target_price=1.0873,
            stop_price=1.0830,
            quantity=1000.0,
            broker_order_id="paper",
        )
    )
    closed = asyncio.run(
        monitor.on_bar(symbol="EURUSD", high=1.0880, low=1.0840, close=1.0875)
    )
    assert len(closed) == 1
    assert closed[0].reason == "TP"
    assert len(monitor.open_positions()) == 0


def test_order_router_paper_mode(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "true")
    router = OrderRouter()
    order = router.submit("MES", "buy", 1.0, order_type="market")
    assert order["status"] == "paper_submitted"
    assert order["broker"] == "webull"
