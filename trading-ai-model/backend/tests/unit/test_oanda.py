"""OANDA credentials, symbol mapping, and execution gating."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import risk.order_sizing_runtime as order_sizing_runtime
from config.execution_config import oanda_live_allowed, resolve_execution_mode
from config.oanda_symbols import is_oanda_tradable, to_instrument
from config.settings import Settings
from live.brokers.base_broker import BrokerOrder
from live.oanda_executor import OandaExecutor, reset_oanda_executor


@pytest.fixture(autouse=True)
def _reset_executor():
    reset_oanda_executor()
    order_sizing_runtime.reset_order_sizing_runtime()
    yield
    reset_oanda_executor()
    order_sizing_runtime.reset_order_sizing_runtime()


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
    with patch("live.oanda_executor.oanda_live_allowed", return_value=False):
        assert ex.can_execute() is False
        result = ex.execute({"symbol": "EURUSD", "action": "buy"})
    assert result["status"] == "blocked"


def test_oanda_executor_converts_order_usd_to_units(monkeypatch):
    monkeypatch.setattr(order_sizing_runtime, "_database_url", lambda: None)
    monkeypatch.setattr(order_sizing_runtime, "_write_postgres", lambda cb, oa: None)
    order_sizing_runtime.set_order_sizing(coinbase_order_usd=5, oanda_order_usd=5)

    ex = OandaExecutor()
    mock_broker = MagicMock()
    mock_broker.place_order = AsyncMock(
        return_value=BrokerOrder(
            broker_order_id="oanda-1",
            symbol="EURUSD",
            side="BUY",
            quantity=4.0,
            order_type="MARKET",
            status="FILLED",
        )
    )
    mock_router = MagicMock()
    mock_router.get.return_value = mock_broker

    with patch("live.oanda_executor.oanda_live_allowed", return_value=True):
        with patch("live.oanda_executor.get_broker_router", return_value=mock_router):
            with patch("risk.risk_runtime.get_risk_engine") as gre:
                gre.return_value = MagicMock()
                result = ex.execute(
                    {
                        "symbol": "EURUSD",
                        "action": "enter_long",
                        "entry": 1.0850,
                        "order_usd": 5.0,
                    }
                )

    assert result["status"] == "filled"
    call_kwargs = mock_broker.place_order.call_args.kwargs
    assert call_kwargs["quantity"] == 4.0
