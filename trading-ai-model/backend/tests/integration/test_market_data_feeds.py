"""Integration tests — mock HTTP for Coinbase + OANDA market-data adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live.broker_adapter import (
    CoinbaseBrokerAdapter,
    OandaBrokerAdapter,
    fetch_latest_bar_for_symbol,
)
from live.market_data_router import clear_market_data_adapter_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_market_data_adapter_cache()
    yield
    clear_market_data_adapter_cache()


@pytest.mark.asyncio
async def test_coinbase_integration_mock_http():
    """Router → CoinbaseBrokerAdapter with mocked REST candles."""
    payload = {
        "candles": [
            {
                "start": "1705000000",
                "open": "0",
                "high": "0",
                "low": "0",
                "close": "0",
                "volume": "0",
            },
            {
                "start": "1705000060",
                "open": "62000",
                "high": "62100",
                "low": "61900",
                "close": "62050",
                "volume": "10",
            },
        ]
    }
    mock_client = MagicMock()
    mock_client.get_candles.return_value = payload

    adapter = CoinbaseBrokerAdapter(client=mock_client)
    bar = await adapter.fetch_latest_bar("BTCUSD")

    assert bar is not None
    assert bar.symbol == "BTCUSD"
    assert bar.close == 62050.0


@pytest.mark.asyncio
async def test_oanda_integration_mock_http():
    """Router → OandaBrokerAdapter with mocked v20 candles HTTP."""
    adapter = OandaBrokerAdapter(api_key="test-token", api_base="https://api-fxpractice.oanda.com")
    payload = {
        "candles": [
            {
                "complete": False,
                "time": "2025-06-01T12:33:00.000000000Z",
                "mid": {"o": "0", "h": "0", "l": "0", "c": "0"},
            },
            {
                "complete": True,
                "time": "2025-06-01T12:34:00.000000000Z",
                "mid": {"o": "1.08450", "h": "1.08480", "l": "1.08440", "c": "1.08465"},
                "volume": 10,
            },
        ]
    }

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: payload

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("live.broker_adapter.httpx.AsyncClient", return_value=mock_client):
        bar = await adapter.fetch_latest_bar("EURUSD")

    assert bar is not None
    assert bar.close == pytest.approx(1.08465)
    assert mock_client.get.call_args.args[0].endswith("/instruments/EUR_USD/candles")


@pytest.mark.asyncio
async def test_fetch_latest_bar_for_symbol_routes_to_oanda(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-token")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    payload = {
        "candles": [
            {
                "complete": True,
                "time": "2025-06-01T12:34:00.000000000Z",
                "mid": {"o": "1.08", "h": "1.09", "l": "1.07", "c": "1.085"},
            }
        ]
    }
    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: payload
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("live.broker_adapter.httpx.AsyncClient", return_value=mock_client):
        bar = await fetch_latest_bar_for_symbol("EURUSD")

    assert bar is not None
    assert bar.close == pytest.approx(1.085)


@pytest.mark.asyncio
async def test_fetch_latest_bar_for_symbol_routes_to_coinbase(monkeypatch):
    monkeypatch.setenv("COINBASE_API_KEY", "organizations/test/key")
    monkeypatch.setenv("COINBASE_API_SECRET", "secret")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")
    from config.settings import get_settings

    get_settings.cache_clear()

    payload = {
        "candles": [
            {
                "start": "1705000000",
                "open": "62000",
                "high": "62100",
                "low": "61900",
                "close": "62050",
                "volume": "10",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.get_candles.return_value = payload

    with patch(
        "live.broker_adapter.build_coinbase_rest_client",
        return_value=mock_client,
    ):
        bar = await fetch_latest_bar_for_symbol("BTCUSD")

    assert bar is not None
    assert bar.close == 62050.0
