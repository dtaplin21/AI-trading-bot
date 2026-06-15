"""Tests for Polygon tick loader parsing and symbol grouping."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from data.loaders.tick_data_loader import (
    Tick,
    TickDataLoader,
    loaders_for_symbols,
    tick_timestamp,
)


def test_tick_timestamp_milliseconds():
    ts = tick_timestamp(1_705_000_000_000)
    assert isinstance(ts, datetime)
    assert ts.tzinfo == timezone.utc


def test_tick_timestamp_nanoseconds():
    ts = tick_timestamp(1_705_000_000_000_000_000)
    assert ts.year >= 2023


def test_parse_forex_quote():
    loader = TickDataLoader(
        symbols=["C:EURUSD"],
        asset_type="forex",
        symbol_map={"C:EURUSD": "EURUSD", "EUR/USD": "EURUSD"},
    )
    tick = loader._parse_message(
        {
            "ev": "C",
            "p": "EUR/USD",
            "bp": 1.0800,
            "ap": 1.0802,
            "as": 1000,
            "t": 1_705_000_000_000,
        }
    )
    assert tick is not None
    assert tick.symbol == "EURUSD"
    assert tick.bid is not None
    assert tick.ask is not None
    assert abs(tick.price - 1.0801) < 1e-6
    assert abs(tick.bid - 1.0800) < 1e-6
    assert abs(tick.ask - 1.0802) < 1e-6


def test_parse_stock_trade():
    loader = TickDataLoader(
        symbols=["TSLA"],
        asset_type="stocks",
        symbol_map={"TSLA": "TSLA"},
    )
    tick = loader._parse_message(
        {"ev": "T", "sym": "TSLA", "p": 250.5, "s": 100, "t": 1_705_000_000_000}
    )
    assert tick is not None
    assert tick.symbol == "TSLA"
    assert abs(tick.price - 250.5) < 1e-6


def test_loaders_for_symbols_groups_asset_classes():
    loaders = loaders_for_symbols(["EURUSD", "TSLA", "BTCUSD"])
    asset_types = {loader.asset_type for loader in loaders}
    assert asset_types == {"forex", "stocks", "crypto"}
    assert sum(len(loader.symbols) for loader in loaders) == 3


def test_subscribe_channel_formats_polygon_tickers():
    forex = TickDataLoader(symbols=["C:EURUSD"], asset_type="forex")
    assert forex._subscribe_channel("C:EURUSD") == "C.EUR/USD"
    assert forex._subscribe_channel("C:USDJPY") == "C.USD/JPY"

    crypto = TickDataLoader(symbols=["X:BTCUSD"], asset_type="crypto")
    assert crypto._subscribe_channel("X:BTCUSD") == "XT.BTC-USD"

    stocks = TickDataLoader(symbols=["TSLA"], asset_type="stocks")
    assert stocks._subscribe_channel("TSLA") == "T.TSLA"

    futures = TickDataLoader(symbols=["C:MES"], asset_type="futures")
    assert futures._subscribe_channel("C:MES") == "T.MES"


def test_parse_crypto_trade():
    loader = TickDataLoader(
        symbols=["X:BTCUSD"],
        asset_type="crypto",
        symbol_map={"X:BTCUSD": "BTCUSD", "BTC-USD": "BTCUSD"},
    )
    tick = loader._parse_message(
        {
            "ev": "XT",
            "pair": "BTC-USD",
            "p": 65000.0,
            "s": 0.1,
            "t": 1_705_000_000_000,
        }
    )
    assert tick is not None
    assert tick.symbol == "BTCUSD"
    assert abs(tick.price - 65000.0) < 1e-6


@pytest.mark.asyncio
async def test_authenticate_ws_waits_past_connected_for_auth_success():
    loader = TickDataLoader(symbols=["X:BTCUSD"], api_key="test-key", asset_type="crypto")
    connected = json.dumps(
        [{"ev": "status", "status": "connected", "message": "Connected Successfully"}]
    )
    auth_ok = json.dumps(
        [{"ev": "status", "status": "auth_success", "message": "authenticated"}]
    )

    ws = AsyncMock()
    ws.recv = AsyncMock(side_effect=[connected, auth_ok])

    assert await loader._authenticate_ws(ws) is True
    ws.send.assert_called_once()
    assert ws.recv.call_count == 2


@pytest.mark.asyncio
async def test_authenticate_ws_fails_on_auth_failed():
    loader = TickDataLoader(symbols=["X:BTCUSD"], api_key="bad-key", asset_type="crypto")
    connected = json.dumps([{"ev": "status", "status": "connected"}])
    auth_fail = json.dumps([{"ev": "status", "status": "auth_failed", "message": "invalid"}])

    ws = AsyncMock()
    ws.recv = AsyncMock(side_effect=[connected, auth_fail])

    assert await loader._authenticate_ws(ws) is False
    assert ws.recv.call_count == 2
