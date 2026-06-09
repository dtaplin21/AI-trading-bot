"""
Tick-level data streaming from Polygon.io.

Primary: REST polling of last trade (works on all API tiers).
Optional: WebSocket stream when `websockets` is installed and
TICK_STREAM_MODE=websocket.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from config.symbols import get_symbol_or_none, massive_symbol
from live.broker_adapter import _load_polygon_ticker_map

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 15.0
DEFAULT_POLL_SECONDS = float(os.getenv("TICK_POLL_INTERVAL_SECONDS", "1.0"))


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: float
    size: float
    timestamp: datetime
    exchange: str = ""


class TickDataLoader:
    """Stream trade ticks for bar assembly."""

    def __init__(
        self,
        api_key: str | None = None,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        ticker_map: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("POLYGON_API_KEY", "")
        self._poll_interval = max(0.25, poll_interval)
        self._ticker_map = ticker_map or _load_polygon_ticker_map()
        self._last_prices: dict[str, float] = {}

    def resolve_ticker(self, symbol: str) -> str:
        sym = symbol.upper()
        if sym in self._ticker_map:
            return self._ticker_map[sym]
        if get_symbol_or_none(sym):
            return massive_symbol(sym)
        return f"C:{sym}"

    async def stream(self, symbol: str) -> AsyncIterator[Tick]:
        """Yield ticks for one symbol until cancelled."""
        mode = os.getenv("TICK_STREAM_MODE", "rest").strip().lower()
        if mode == "websocket":
            try:
                async for tick in self._stream_websocket(symbol):
                    yield tick
                return
            except Exception as exc:
                logger.warning(
                    "TickDataLoader[%s]: websocket failed (%s) — falling back to REST",
                    symbol,
                    exc,
                )
        async for tick in self._stream_rest(symbol):
            yield tick

    async def _stream_rest(self, symbol: str) -> AsyncIterator[Tick]:
        if not self._api_key:
            logger.warning("TickDataLoader: POLYGON_API_KEY not set — no ticks")
            return

        ticker = self.resolve_ticker(symbol)
        url = f"https://api.polygon.io/v2/last/trade/{ticker}"
        sym = symbol.upper()

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while True:
                try:
                    response = await client.get(
                        url, params={"apiKey": self._api_key}
                    )
                    response.raise_for_status()
                    payload = response.json()
                    result = payload.get("results") or {}
                    price = float(result.get("p") or result.get("price") or 0)
                    if price <= 0:
                        await asyncio.sleep(self._poll_interval)
                        continue

                    ts_ns = result.get("t") or result.get("sip_timestamp")
                    if ts_ns:
                        ts = datetime.fromtimestamp(
                            float(ts_ns) / 1_000_000_000.0, tz=timezone.utc
                        )
                    else:
                        ts = datetime.now(tz=timezone.utc)

                    size = float(result.get("s") or result.get("size") or 0)
                    exchange = str(result.get("x") or result.get("exchange") or "")

                    if self._last_prices.get(sym) != price:
                        self._last_prices[sym] = price
                        yield Tick(
                            symbol=sym,
                            price=price,
                            size=size,
                            timestamp=ts,
                            exchange=exchange,
                        )
                except httpx.HTTPError as exc:
                    logger.debug("TickDataLoader[%s] poll error: %s", sym, exc)

                await asyncio.sleep(self._poll_interval)

    async def _stream_websocket(self, symbol: str) -> AsyncIterator[Tick]:
        import json

        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("pip install websockets for TICK_STREAM_MODE=websocket") from exc

        ticker = self.resolve_ticker(symbol)
        sym = symbol.upper()
        uri = "wss://socket.polygon.io/stocks"

        async with websockets.connect(uri, ping_interval=20) as ws:
            await ws.send(json.dumps({"action": "auth", "params": self._api_key}))
            await ws.send(
                json.dumps({"action": "subscribe", "params": f"T.{ticker}"})
            )

            async for raw in ws:
                msgs = json.loads(raw)
                if not isinstance(msgs, list):
                    msgs = [msgs]
                for msg in msgs:
                    if msg.get("ev") != "T":
                        continue
                    price = float(msg.get("p", 0))
                    if price <= 0:
                        continue
                    ts_ms = msg.get("t")
                    ts = (
                        datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                        if ts_ms
                        else datetime.now(tz=timezone.utc)
                    )
                    yield Tick(
                        symbol=sym,
                        price=price,
                        size=float(msg.get("s", 0)),
                        timestamp=ts,
                        exchange=str(msg.get("x", "")),
                    )
