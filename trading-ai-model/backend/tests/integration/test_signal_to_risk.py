"""Signal to risk integration test."""

from risk.risk_engine import PortfolioState, RiskEngine
from signal_engine.signal_schema import TradingSignal
from tests.fixtures.sample_signals import SAMPLE_SIGNAL


def test_signal_to_risk_flow():
    signal = TradingSignal(**SAMPLE_SIGNAL)
    engine = RiskEngine()
    decision = engine.evaluate(signal.signal_rank, PortfolioState(), signal.symbol)
    assert decision.approved is True
