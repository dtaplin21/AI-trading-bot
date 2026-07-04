"""
data/loaders/coinbase_crypto_loader.py

Crypto tick stream via Coinbase Advanced Trade — avoids Polygon crypto when creds exist.

Default:
  Poll authenticated product candles every COINBASE_PRICING_POLL_SEC (uses latest M1 close)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncGenerator, AsyncIterator

from config.coinbase_symbols import is_coinbase_tradable
from data.loaders.tick_data_loader import Tick
from live.broker_adapter import CoinbaseBrokerAdapter

logger = logging.getLogger(__name__)

DEFAULT_POLL_SEC = float(os.getenv("COINBASE_PRICING_POLL_SEC", "2.0"))


class CoinbaseCryptoTickLoader:
    """Yield Tick objects for Coinbase crypto pairs (never Polygon when configured)."""

    def __init__(
        self,
        symbols: list[str],
        *,
        poll_interval: float = DEFAULT_POLL_SEC,
        adapter: CoinbaseBrokerAdapter | None = None,
    ) -> None:
        self.symbols = [s.upper() for s in symbols if is_coinbase_tradable(s)]
        self._adapter = adapter or CoinbaseBrokerAdapter()
        self._poll_interval = max(0.5, poll_interval)
        self._running = False
        self._last_prices: dict[str, float] = {}

    async def stream(self) -> AsyncGenerator[Tick, None]:
        self._running = True
        async for tick in self._poll_candles():
            if not self._running:
                break
            yield tick

    async def _poll_candles(self) -> AsyncIterator[Tick]:
        while self._running:
            for sym in self.symbols:
                try:
                    bar = await self._adapter.fetch_latest_bar(sym, "1m")
                except Exception as exc:
                    logger.debug("CoinbaseCryptoTickLoader[%s] poll error: %s", sym, exc)
                    continue
                if bar is None or bar.close <= 0:
                    continue
                if self._last_prices.get(sym) == bar.close:
                    continue
                self._last_prices[sym] = bar.close
                yield Tick(
                    symbol=sym,
                    price=bar.close,
                    size=bar.volume,
                    timestamp=bar.timestamp,
                )
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False
