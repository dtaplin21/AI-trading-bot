"""Tests for full RiskEngine."""

from datetime import datetime, timezone
from typing import Any

import pytest

from pipeline.confluence_report import ConfluenceReport
from pipeline.schemas import FusedFeatureSet, TradeAction, TradePlan
from paper_trading.position_book import reset_position_book
from risk.risk_engine import PortfolioState, RiskEngine


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
    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
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
