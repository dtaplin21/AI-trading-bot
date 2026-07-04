"""Tests for live broker market-data adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from live.broker_adapter import (
    OandaBrokerAdapter,
    PolygonBrokerAdapter,
    default_worker_broker,
    fetch_latest_bar_for_symbol,
    get_broker_adapter,
    parse_oanda_candle,
)


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
async def test_fetch_latest_bar_for_symbol_prefers_oanda_for_forex(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-token")
    oanda_bar = AsyncMock()
    oanda_bar.fetch_latest_bar = AsyncMock(
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
    polygon = AsyncMock()
    polygon.broker_id = "polygon"
    polygon.fetch_latest_bar = AsyncMock(return_value=None)

    bar = await fetch_latest_bar_for_symbol(
        "EURUSD",
        broker="polygon",
        oanda_adapter=oanda_bar,
        primary_adapter=polygon,
    )
    assert bar is not None
    assert bar.close == pytest.approx(1.0845)
    oanda_bar.fetch_latest_bar.assert_awaited_once()
    polygon.fetch_latest_bar.assert_not_awaited()


def test_default_worker_broker_prefers_polygon(monkeypatch):
    monkeypatch.setenv("BROKER", "")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")
    assert default_worker_broker() == "polygon"


def test_default_worker_broker_falls_back_paper(monkeypatch):
    monkeypatch.delenv("BROKER", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    assert default_worker_broker() == "paper"
