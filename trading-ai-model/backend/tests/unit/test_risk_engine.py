"""Tests for risk engine."""

from risk.risk_engine import PortfolioState, RiskEngine


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
