"""Tests for Coinbase crypto tick loader."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.loaders.coinbase_crypto_loader import CoinbaseCryptoTickLoader
from live.broker_adapter import CoinbaseBrokerAdapter
from pipeline.schemas import OHLCV


@pytest.mark.asyncio
async def test_coinbase_crypto_loader_poll_yields_tick():
    bar = OHLCV(
        symbol="BTCUSD",
        timeframe="1m",
        timestamp=datetime(2025, 6, 1, 12, 34, tzinfo=timezone.utc),
        open=62000.0,
        high=62100.0,
        low=61900.0,
        close=62050.0,
        volume=10.0,
    )
    mock_adapter = MagicMock(spec=CoinbaseBrokerAdapter)
    mock_adapter.fetch_latest_bar = AsyncMock(return_value=bar)

    loader = CoinbaseCryptoTickLoader(
        symbols=["BTCUSD"],
        poll_interval=0.5,
        adapter=mock_adapter,
    )

    ticks = []

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
    assert ticks[0].symbol == "BTCUSD"
    assert ticks[0].price == pytest.approx(62050.0)
