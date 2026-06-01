"""Tests for live broker market-data adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from live.broker_adapter import (
    PolygonBrokerAdapter,
    default_worker_broker,
    get_broker_adapter,
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


def test_default_worker_broker_prefers_polygon(monkeypatch):
    monkeypatch.setenv("BROKER", "")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")
    assert default_worker_broker() == "polygon"


def test_default_worker_broker_falls_back_paper(monkeypatch):
    monkeypatch.delenv("BROKER", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    assert default_worker_broker() == "paper"
