"""Coinbase executor — blocked when live not enabled."""

from unittest.mock import MagicMock, patch

import pytest

from config.settings import Settings
from live.coinbase_executor import CoinbaseExecutor, reset_coinbase_client


@pytest.fixture(autouse=True)
def _reset():
    reset_coinbase_client()
    yield
    reset_coinbase_client()


def test_blocks_when_paper_enabled():
    ex = CoinbaseExecutor()
    with patch("live.coinbase_executor.get_settings") as gs:
        gs.return_value = Settings(paper_trading_enabled=True, coinbase_live_enabled=True)
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
    mock_client = MagicMock()
    mock_order = MagicMock(success=True, success_response=MagicMock(order_id="ord-1"))
    mock_client.market_order_buy.return_value = mock_order

    with patch("live.coinbase_executor.coinbase_live_allowed", return_value=True):
        with patch("live.coinbase_executor._get_client", return_value=mock_client):
            with patch("live.coinbase_executor.get_settings") as gs:
                gs.return_value = Settings(
                    paper_trading_enabled=False,
                    coinbase_live_enabled=True,
                    coinbase_max_order_usd=50,
                )
                result = ex.execute(
                    {
                        "symbol": "BTCUSD",
                        "action": "enter_long",
                        "quote_size_usd": 40,
                    }
                )

    assert result["status"] == "filled"
    assert result["order_id"] == "ord-1"
    mock_client.market_order_buy.assert_called_once()
