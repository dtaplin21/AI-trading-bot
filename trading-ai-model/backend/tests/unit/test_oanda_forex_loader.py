"""Tests for OANDA forex tick loader."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest

from data.loaders.oanda_forex_loader import OandaForexTickLoader


@pytest.mark.asyncio
async def test_oanda_forex_loader_poll_yields_tick():
    loader = OandaForexTickLoader(
        symbols=["EURUSD"],
        api_key="test-token",
        api_base="https://api-fxpractice.oanda.com",
        poll_interval=0.5,
    )
    payload = {
        "candles": [
            {
                "complete": False,
                "time": "2025-06-01T12:34:00.000000000Z",
                "mid": {"o": "1.08450", "h": "1.08480", "l": "1.08440", "c": "1.08465"},
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

    ticks = []
    with patch("data.loaders.oanda_forex_loader.httpx.AsyncClient", return_value=mock_client):

        async def collect():
            count = 0
            async for tick in loader.stream():
                ticks.append(tick)
                count += 1
                if count >= 1:
                    loader.stop()
                    break

        await collect()

    assert len(ticks) == 1
    assert ticks[0].symbol == "EURUSD"
    assert ticks[0].price == pytest.approx(1.08465)


def test_parse_pricing_line_mid():
    loader = OandaForexTickLoader(symbols=["EURUSD"], api_key="k")
    line = json.dumps(
        {
            "type": "PRICE",
            "instrument": "EUR_USD",
            "time": "2025-06-01T12:34:00.000000000Z",
            "bids": [{"price": "1.08450", "liquidity": 1000000}],
            "asks": [{"price": "1.08470", "liquidity": 1000000}],
        }
    )
    tick = loader._parse_pricing_line(line)
    assert tick is not None
    assert tick.symbol == "EURUSD"
    assert tick.price == pytest.approx(1.08460)


@pytest.mark.asyncio
async def test_oanda_forex_loader_logs_401_once(caplog):
    import httpx

    loader = OandaForexTickLoader(
        symbols=["EURUSD"],
        api_key="bad-token",
        api_base="https://api-fxpractice.oanda.com",
        poll_interval=0.5,
    )

    request = httpx.Request("GET", "https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/candles")
    response = httpx.Response(401, request=request)

    def _raise_unauthorized():
        raise httpx.HTTPStatusError("Unauthorized", request=request, response=response)

    mock_response = MagicMock()
    mock_response.raise_for_status = _raise_unauthorized

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("data.loaders.oanda_forex_loader.httpx.AsyncClient", return_value=mock_client):
        async def collect():
            async for _tick in loader.stream():
                pass
            loader.stop()

        loader.stop()
        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        loader.stop()
        await task

    assert "401 Unauthorized" in caplog.text
