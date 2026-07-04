"""Tests for live broker market-data adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from live.broker_adapter import (
    CoinbaseBrokerAdapter,
    OandaBrokerAdapter,
    PolygonBrokerAdapter,
    default_worker_broker,
    fetch_latest_bar_for_symbol,
    get_broker_adapter,
    parse_coinbase_candle,
    parse_oanda_candle,
)


@pytest.mark.asyncio
async def test_polygon_blocks_forex_when_oanda_creds(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-token")
    adapter = PolygonBrokerAdapter(api_key="test-key")
    bar = await adapter.fetch_latest_bar("EURUSD")
    assert bar is None


@pytest.mark.asyncio
async def test_polygon_fetch_latest_bar():
    adapter = PolygonBrokerAdapter(
        api_key="test-key",
        ticker_map={"MES": "C:MES", "BTCUSD": "X:BTCUSD", "TSLA": "TSLA"},
    )
    payload = {
        "results": [
            {
                "T": 1_705_000_000_000,
                "o": 5400.0,
                "h": 5402.0,
                "l": 5398.0,
                "c": 5401.0,
                "v": 1200,
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
        bar = await adapter.fetch_latest_bar("MES")

    assert bar is not None
    assert bar.symbol == "MES"
    assert bar.close == 5401.0
    assert bar.timeframe == "1m"


def test_polygon_resolve_ticker_from_symbols_registry():
    adapter = PolygonBrokerAdapter(api_key="test")
    assert adapter.resolve_ticker("EURUSD") == "C:EURUSD"
    assert adapter.resolve_ticker("BTCUSD") == "X:BTCUSD"
    assert adapter.resolve_ticker("NVDA") == "NVDA"


def test_get_broker_adapter_polygon():
    adapter = get_broker_adapter("polygon")
    assert adapter.broker_id == "polygon"


def test_get_broker_adapter_oanda():
    adapter = get_broker_adapter("oanda")
    assert adapter.broker_id == "oanda"


def test_get_broker_adapter_coinbase():
    adapter = get_broker_adapter("coinbase")
    assert adapter.broker_id == "coinbase"
    assert isinstance(adapter, CoinbaseBrokerAdapter)


def test_parse_coinbase_candle():
    candle = {
        "start": "1705000000",
        "open": "62000.0",
        "high": "62100.0",
        "low": "61900.0",
        "close": "62050.0",
        "volume": "12.5",
    }
    bar = parse_coinbase_candle("BTCUSD", candle)
    assert bar is not None
    assert bar.symbol == "BTCUSD"
    assert bar.close == 62050.0


def test_parse_coinbase_candle_rejects_zero_close():
    candle = {
        "start": "1705000000",
        "open": "0",
        "high": "0",
        "low": "0",
        "close": "0",
        "volume": "0",
    }
    assert parse_coinbase_candle("BTCUSD", candle) is None


@pytest.mark.asyncio
async def test_coinbase_fetch_latest_bar():
    adapter = CoinbaseBrokerAdapter()
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

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: payload

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("live.broker_adapter.httpx.AsyncClient", return_value=mock_client):
        bar = await adapter.fetch_latest_bar("BTCUSD")

    assert bar is not None
    assert bar.close == 62050.0
    call_url = mock_client.get.call_args.args[0]
    assert "BTC-USD" in call_url
    assert "/market/products/" in call_url


def test_parse_oanda_candle():
    candle = {
        "complete": True,
        "time": "2025-06-01T12:34:00.000000000Z",
        "mid": {"o": "1.08450", "h": "1.08480", "l": "1.08440", "c": "1.08465"},
        "volume": 42,
    }
    bar = parse_oanda_candle("EURUSD", candle)
    assert bar is not None
    assert bar.symbol == "EURUSD"
    assert bar.close == pytest.approx(1.08465)


def test_parse_oanda_candle_rejects_incomplete_by_default():
    candle = {
        "complete": False,
        "time": "2025-06-01T12:34:00.000000000Z",
        "mid": {"o": "1.08450", "h": "1.08480", "l": "1.08440", "c": "1.08465"},
    }
    assert parse_oanda_candle("EURUSD", candle) is None


def test_parse_oanda_candle_allow_incomplete():
    candle = {
        "complete": False,
        "time": "2025-06-01T12:34:00.000000000Z",
        "mid": {"o": "1.08450", "h": "1.08480", "l": "1.08440", "c": "1.08465"},
    }
    bar = parse_oanda_candle("EURUSD", candle, allow_incomplete=True)
    assert bar is not None
    assert bar.close == pytest.approx(1.08465)


def test_parse_oanda_candle_rejects_zero_close():
    candle = {
        "complete": True,
        "time": "2025-06-01T12:34:00.000000000Z",
        "mid": {"o": "0", "h": "0", "l": "0", "c": "0"},
    }
    assert parse_oanda_candle("EURUSD", candle) is None


@pytest.mark.asyncio
async def test_oanda_fetch_latest_bar():
    adapter = OandaBrokerAdapter(api_key="test-token", api_base="https://api-fxpractice.oanda.com")
    payload = {
        "candles": [
            {
                "complete": False,
                "time": "2025-06-01T12:33:00.000000000Z",
                "mid": {"o": "1.08", "h": "1.09", "l": "1.07", "c": "1.085"},
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
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_fetch_latest_bar_for_symbol_uses_router(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-token")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    mock_oanda = AsyncMock(spec=OandaBrokerAdapter)
    mock_oanda.broker_id = "oanda"
    mock_oanda.fetch_latest_bar = AsyncMock(
        return_value=type(
            "Bar",
            (),
            {
                "symbol": "EURUSD",
                "timeframe": "1m",
                "timestamp": None,
                "open": 1.08,
                "high": 1.09,
                "low": 1.07,
                "close": 1.0845,
                "volume": 0.0,
            },
        )()
    )

    bar = await fetch_latest_bar_for_symbol("EURUSD", adapter=mock_oanda)
    assert bar is not None
    assert bar.close == pytest.approx(1.0845)
    mock_oanda.fetch_latest_bar.assert_awaited_once_with("EURUSD", "1m")


def test_default_worker_broker_prefers_polygon(monkeypatch):
    monkeypatch.setenv("BROKER", "")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")
    assert default_worker_broker() == "polygon"


def test_default_worker_broker_falls_back_paper(monkeypatch):
    monkeypatch.delenv("BROKER", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    assert default_worker_broker() == "paper"
