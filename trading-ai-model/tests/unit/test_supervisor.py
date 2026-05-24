"""Tests for multi-agent supervisor pipeline."""

from agents.method_agents import ALL_METHOD_AGENTS, REQUIRED_METHODS
from agents.supervisor import TradingSupervisor
from tests.fixtures.sample_ohlcv import sample_ohlcv


def test_all_method_agents_registered():
    assert len(ALL_METHOD_AGENTS) == len(REQUIRED_METHODS)


def test_supervisor_runs_all_methods():
    supervisor = TradingSupervisor()
    decision = supervisor.process_candle("MES", sample_ohlcv(60), historical_sample_size=1420)
    assert decision.fused_features is not None
    assert decision.fused_features.methods_run == len(REQUIRED_METHODS)
    assert decision.fused_features.methods_skipped == 0
    assert decision.audit is not None
    assert len(decision.audit.reasons) >= 1


def test_risk_veto_blocks_execution_without_approval():
    supervisor = TradingSupervisor()
    from risk.risk_engine import PortfolioState

    bad_portfolio = PortfolioState(daily_pnl_pct=-5.0)
    decision = supervisor.process_candle(
        "MES", sample_ohlcv(60), portfolio=bad_portfolio, execute=True
    )
    assert decision.risk is not None
    assert decision.risk.approved is False
    assert decision.execution is None or not decision.execution.executed


def test_signal_rank_in_valid_range():
    supervisor = TradingSupervisor()
    decision = supervisor.process_candle("MES", sample_ohlcv(60), historical_sample_size=500)
    assert 0 <= decision.signal_rank <= 100
