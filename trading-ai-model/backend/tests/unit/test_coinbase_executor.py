"""Coinbase executor — blocked when live not enabled."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import risk.order_sizing_runtime as order_sizing_runtime
from config.settings import Settings
from live.brokers.base_broker import BrokerOrder
from live.coinbase_executor import CoinbaseExecutor, reset_coinbase_client


@pytest.fixture(autouse=True)
def _reset():
    reset_coinbase_client()
    order_sizing_runtime.reset_order_sizing_runtime()
    yield
    reset_coinbase_client()
    order_sizing_runtime.reset_order_sizing_runtime()


def test_blocks_when_paper_enabled():
    ex = CoinbaseExecutor()
    with patch("live.coinbase_executor.coinbase_live_allowed", return_value=False):
        result = ex.execute({"symbol": "BTCUSD", "action": "enter_long", "quote_size_usd": 25})
    assert result["status"] == "blocked"


def test_skips_non_crypto():
    ex = CoinbaseExecutor()
    with patch("live.coinbase_executor.coinbase_live_allowed", return_value=True):
        result = ex.execute({"symbol": "MES", "action": "enter_long"})
    assert result["status"] == "skipped"


def test_market_buy_when_live(monkeypatch):
    monkeypatch.setenv("COINBASE_API_KEY", "test-key")
    monkeypatch.setenv("COINBASE_API_SECRET", "test-secret")
    ex = CoinbaseExecutor()
    mock_broker = MagicMock()
    mock_broker.place_order = AsyncMock(
        return_value=BrokerOrder(
            broker_order_id="ord-1",
            symbol="BTCUSD",
            side="BUY",
            quantity=0.001,
            order_type="MARKET",
            status="FILLED",
        )
    )
    mock_router = MagicMock()
    mock_router.get.return_value = mock_broker

    with patch("live.coinbase_executor.coinbase_live_allowed", return_value=True):
        with patch("live.coinbase_executor.get_broker_router", return_value=mock_router):
            with patch("live.coinbase_executor.get_settings") as gs:
                gs.return_value = Settings(
                    paper_trading_enabled=False,
                    coinbase_live_enabled=True,
                    coinbase_max_order_usd=50,
                )
                with patch("live.coinbase_executor.coinbase_order_usd", return_value=50.0):
                    with patch("risk.risk_runtime.get_risk_engine") as gre:
                        gre.return_value = MagicMock()
                        result = ex.execute(
                            {
                                "symbol": "BTCUSD",
                                "action": "enter_long",
                                "quote_size_usd": 40,
                                "entry": 40000.0,
                            }
                        )

    assert result["status"] == "filled"
    assert result["order_id"] == "ord-1"
    mock_broker.place_order.assert_called_once()


def test_uses_runtime_default_when_quote_missing(monkeypatch):
    monkeypatch.setenv("COINBASE_API_KEY", "test-key")
    monkeypatch.setenv("COINBASE_API_SECRET", "test-secret")
    monkeypatch.setattr(order_sizing_runtime, "_database_url", lambda: None)
    monkeypatch.setattr(order_sizing_runtime, "_write_postgres", lambda cb, oa: None)
    order_sizing_runtime.set_order_sizing(coinbase_order_usd=5, oanda_order_usd=5)

    ex = CoinbaseExecutor()
    mock_broker = MagicMock()
    mock_broker.place_order = AsyncMock(
        return_value=BrokerOrder(
            broker_order_id="ord-2",
            symbol="BTCUSD",
            side="BUY",
            quantity=0.000125,
            order_type="MARKET",
            status="FILLED",
        )
    )
    mock_router = MagicMock()
    mock_router.get.return_value = mock_broker

    with patch("live.coinbase_executor.coinbase_live_allowed", return_value=True):
        with patch("live.coinbase_executor.get_broker_router", return_value=mock_router):
            with patch("live.coinbase_executor.get_settings") as gs:
                gs.return_value = Settings(
                    paper_trading_enabled=False,
                    coinbase_live_enabled=True,
                    coinbase_max_order_usd=50,
                )
                with patch("risk.risk_runtime.get_risk_engine") as gre:
                    gre.return_value = MagicMock()
                    result = ex.execute(
                        {
                            "symbol": "BTCUSD",
                            "action": "enter_long",
                            "entry": 40000.0,
                        }
                    )

    assert result["status"] == "filled"
    assert result["quote_size_usd"] == 5.0
    call_kwargs = mock_broker.place_order.call_args.kwargs
    assert call_kwargs["quantity"] == pytest.approx(5.0 / 40000.0)
