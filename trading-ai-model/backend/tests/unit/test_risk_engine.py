"""Tests for full RiskEngine."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

import risk.kill_switch_runtime as kill_switch_runtime
import risk.order_sizing_runtime as order_sizing_runtime
from pipeline.confluence_report import ConfluenceReport
from pipeline.schemas import FusedFeatureSet, TradeAction, TradePlan
from paper_trading.position_book import reset_position_book
from risk.risk_engine import PortfolioState, RiskEngine


@pytest.fixture(autouse=True)
def kill_switch_off(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (None, None))
    kill_switch_runtime.reset_kill_switch_runtime()
    monkeypatch.setattr(order_sizing_runtime, "_database_url", lambda: None)
    order_sizing_runtime.reset_order_sizing_runtime()
    yield
    kill_switch_runtime.reset_kill_switch_runtime()
    order_sizing_runtime.reset_order_sizing_runtime()


def _plan(action=TradeAction.ENTER_LONG) -> TradePlan:
    return TradePlan(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        action=action,
        entry_price=5000.0,
        stop_loss=4990.0,
        take_profit=5020.0,
    )


def _fused(**kwargs: Any) -> FusedFeatureSet:
    base = FusedFeatureSet(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        signal_rank=0,
    )
    return base.model_copy(update=kwargs) if kwargs else base


def _confluence(**kwargs: Any) -> ConfluenceReport:
    base = ConfluenceReport(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(tz=timezone.utc),
        regime="trend_up",
        conflict_score=0.10,
        news_trading_blocked=False,
    )
    return base.model_copy(update=kwargs) if kwargs else base


def test_rejects_daily_loss():
    engine = RiskEngine()
    state = PortfolioState(daily_pnl_pct=-3.0)
    decision = engine.evaluate(80, state)
    assert decision.approved is False


def test_approves_valid_signal():
    engine = RiskEngine()
    state = PortfolioState()
    decision = engine.evaluate(80, state, symbol="MES")
    assert decision.approved is True
    assert decision.max_position_size > 0


def test_kill_switch_rejects(monkeypatch):
    reset_position_book()
    with patch("risk.risk_engine.is_kill_switch_active", return_value=True):
        engine = RiskEngine()
        result = engine.approve(
            plan=_plan(),
            fused=_fused(),
            confluence=_confluence(),
            p_success=0.70,
            ev_dollars=10.0,
            sample_size=500,
            signal_rank=75,
        )
    assert result.approved is False
    assert result.kill_switch_active is True
    assert any("KILL SWITCH" in r for r in result.rejection_reasons)


def test_approve_passes_valid_trade():
    reset_position_book()
    engine = RiskEngine()
    result = engine.approve(
        plan=_plan(),
        fused=_fused(),
        confluence=_confluence(),
        p_success=0.70,
        ev_dollars=10.0,
        sample_size=500,
        signal_rank=75,
    )
    assert result.approved is True
    assert result.position_size_contracts >= 1


def test_rejects_correlated_open_position():
    reset_position_book()
    from paper_trading.position_book import get_position_book

    get_position_book().open_position(
        symbol="NQ",
        direction="long",
        entry_price=19000.0,
        stop_loss=18950.0,
        take_profit=19100.0,
        quantity=1,
    )
    engine = RiskEngine()
    result = engine.approve(
        plan=_plan(),
        fused=_fused(),
        confluence=_confluence(),
        p_success=0.70,
        ev_dollars=10.0,
        sample_size=500,
        signal_rank=75,
    )
    assert result.approved is False
    assert any("correlation" in r.lower() for r in result.rejection_reasons)


def test_rejects_wait_action():
    reset_position_book()
    engine = RiskEngine()
    result = engine.approve(
        plan=_plan(action=TradeAction.WAIT),
        fused=_fused(),
        confluence=_confluence(),
        p_success=0.70,
        ev_dollars=10.0,
        sample_size=500,
        signal_rank=75,
    )
    assert result.approved is False


def test_record_outcome_tracks_consecutive_losses():
    engine = RiskEngine()
    engine.record_outcome(-50.0)
    engine.record_outcome(-30.0)
    assert engine._consecutive_losses == 2
    engine.record_outcome(100.0)
    assert engine._consecutive_losses == 0


def test_approve_uses_runtime_coinbase_order_usd(monkeypatch):
    reset_position_book()
    monkeypatch.setattr(order_sizing_runtime, "_write_postgres", lambda cb, oa: None)
    order_sizing_runtime.set_order_sizing(coinbase_order_usd=25, oanda_order_usd=10)

    engine = RiskEngine()
    plan = _plan()
    plan = plan.model_copy(update={"symbol": "BTCUSD"})
    result = engine.approve(
        plan=plan,
        fused=_fused(symbol="BTCUSD"),
        confluence=_confluence(symbol="BTCUSD"),
        p_success=0.70,
        ev_dollars=10.0,
        sample_size=500,
        signal_rank=75,
    )
    assert result.approved is True
    assert result.max_notional_usd == 25.0
    assert result.oanda_order_usd == 0.0


def test_risk_summary_includes_order_sizing(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "7")
    order_sizing_runtime.reset_order_sizing_runtime()
    engine = RiskEngine()
    summary = engine.risk_summary()
    assert summary["coinbase_order_usd"] == 7.0
    assert summary["oanda_order_usd"] == 7.0
    assert "order_sizing_limits" in summary
