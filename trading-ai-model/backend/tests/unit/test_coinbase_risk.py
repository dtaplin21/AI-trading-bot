"""Coinbase risk caps and execution gating."""

import pytest

from config.execution_config import coinbase_live_allowed, resolve_execution_mode
from config.settings import Settings
from risk.risk_engine import RiskEngine, PortfolioState
from risk.risk_runtime import reset_risk_engine


@pytest.fixture(autouse=True)
def _reset_engine():
    reset_risk_engine()
    yield
    reset_risk_engine()


def test_daily_loss_usd_limit(monkeypatch):
    monkeypatch.setenv("RISK_ACCOUNT_CAP_USD", "500")
    monkeypatch.setenv("RISK_MAX_DAILY_LOSS_USD", "30")
    engine = RiskEngine()
    engine._daily_limiter.record_trade(-31.0)
    hit, _ = engine._daily_limiter.is_limit_hit()
    assert hit is True
    assert engine._daily_limiter.daily_loss_limit_usd() == 30.0


def test_account_cap_in_summary(monkeypatch):
    monkeypatch.setenv("RISK_ACCOUNT_CAP_USD", "500")
    monkeypatch.setenv("RISK_MAX_DAILY_LOSS_USD", "30")
    engine = RiskEngine()
    summary = engine.risk_summary()
    assert summary["account_cap_usd"] == 500
    assert summary["max_daily_loss_usd"] == 30


def test_evaluate_uses_cap_account_size(monkeypatch):
    monkeypatch.setenv("RISK_ACCOUNT_CAP_USD", "500")
    monkeypatch.setenv("RISK_MAX_DAILY_LOSS_USD", "30")
    engine = RiskEngine()
    state = PortfolioState(account_size=500, daily_pnl_pct=-7.0)
    decision = engine.evaluate(80, state)
    assert decision.approved is False
    assert decision.reason == "daily_loss_limit"


def test_paper_mode_by_default():
    s = Settings(paper_trading_enabled=True, coinbase_live_enabled=True)
    assert resolve_execution_mode(s) == "paper"
    assert coinbase_live_allowed(s) is False


def test_coinbase_live_requires_all_flags():
    s = Settings(
        paper_trading_enabled=False,
        coinbase_live_enabled=True,
        coinbase_api_key="key",
        coinbase_api_secret="secret",
        enabled_brokers="coinbase",
    )
    assert resolve_execution_mode(s) == "coinbase"
    assert coinbase_live_allowed(s) is True


def test_coinbase_product_map():
    from config.coinbase_symbols import to_product_id, is_coinbase_tradable

    assert to_product_id("BTCUSD") == "BTC-USD"
    assert is_coinbase_tradable("BTCUSD") is True
    assert is_coinbase_tradable("MES") is False
