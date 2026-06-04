"""OANDA credentials, symbol mapping, and execution gating."""

import pytest

from config.execution_config import oanda_live_allowed, resolve_execution_mode
from config.oanda_symbols import is_oanda_tradable, to_instrument
from config.settings import Settings
from live.oanda_executor import OandaExecutor, reset_oanda_executor


@pytest.fixture(autouse=True)
def _reset_executor():
    reset_oanda_executor()
    yield
    reset_oanda_executor()


def test_oanda_instrument_map():
    assert to_instrument("EURUSD") == "EUR_USD"
    assert is_oanda_tradable("EURUSD") is True
    assert is_oanda_tradable("MES") is False


def test_paper_mode_blocks_oanda_live():
    s = Settings(
        paper_trading_enabled=True,
        oanda_live_enabled=True,
        oanda_api_key="token",
        enabled_brokers="oanda",
    )
    assert resolve_execution_mode(s) == "paper"
    assert oanda_live_allowed(s) is False


def test_oanda_live_requires_all_flags():
    s = Settings(
        paper_trading_enabled=False,
        oanda_live_enabled=True,
        oanda_api_key="token",
        enabled_brokers="oanda",
    )
    assert resolve_execution_mode(s) == "oanda"
    assert oanda_live_allowed(s) is True


def test_onda_api_key_alias_on_settings():
    s = Settings(oanda_api_key="from-field")
    assert s.oanda_api_key == "from-field"


def test_oanda_executor_blocked_when_not_live():
    ex = OandaExecutor()
    assert ex.can_execute() is False
    result = ex.execute({"symbol": "EURUSD", "action": "buy"})
    assert result["status"] == "blocked"
