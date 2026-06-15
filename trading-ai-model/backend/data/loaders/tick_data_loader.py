"""
data/loaders/tick_data_loader.py

Streams live ticks from Polygon WebSocket and yields them.

Was: yield from ()  — yielded nothing
Now: real WebSocket subscription to Polygon tick stream

Connects to:
  - ChartWatchRunner — consumes tick stream for live bar assembly
  - TickAggregator — receives ticks and builds OHLCV bars
  - TimeseriesStore — saves completed bars to TimescaleDB
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncGenerator, AsyncIterator, Optional

import httpx

from config.symbols import (
    get_symbol_or_none,
    massive_symbol,
    normalize_symbol,
    polygon_ticker_map,
)

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 15.0
DEFAULT_POLL_SECONDS = float(os.getenv("TICK_POLL_INTERVAL_SECONDS", "1.0"))

_ASSET_CLASS_TO_WS = {
    "forex": "forex",
    "crypto": "crypto",
    "equity": "stocks",
    "futures": "futures",
}


def _forex_ws_pair(body: str) -> str:
    """EURUSD → EUR/USD for Polygon forex quote subscriptions (C.EUR/USD)."""
    normalized = body.upper().replace("/", "").replace("-", "").strip()
    if len(normalized) == 6:
        return f"{normalized[:3]}/{normalized[3:]}"
    return body


def _crypto_ws_pair(body: str) -> str:
    """BTCUSD → BTC-USD for Polygon crypto trade subscriptions (XT.BTC-USD)."""
    normalized = body.upper().replace("/", "").replace("-", "").strip()
    if normalized.endswith("USD") and len(normalized) > 3:
        return f"{normalized[:-3]}-USD"
    if len(normalized) == 6:
        return f"{normalized[:3]}-{normalized[3:]}"
    return body


def tick_timestamp(raw) -> datetime:
    """Normalize Polygon tick time (ns, ms, or seconds) to UTC datetime."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    ts = float(raw or 0)
    if ts <= 0:
        return datetime.now(tz=timezone.utc)
    if ts > 1e15:
        return datetime.fromtimestamp(ts / 1_000_000_000.0, tz=timezone.utc)
    if ts > 1e12:
        return datetime.fromtimestamp(ts / 1_000.0, tz=timezone.utc)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class Tick:
    """A single price tick from the market."""

    __slots__ = ("symbol", "price", "size", "timestamp", "bid", "ask")

    def __init__(self, symbol, price, size, timestamp, bid=None, ask=None):
        self.symbol = symbol.upper()
        self.price = float(price)
        self.size = float(size)
        self.timestamp = tick_timestamp(timestamp)
        self.bid = float(bid) if bid is not None else None
        self.ask = float(ask) if ask is not None else None

    def __repr__(self):
        return f"Tick({self.symbol} @ {self.price} sz={self.size})"


class TickDataLoader:
    """
    Streams live ticks from Polygon WebSocket.

    Usage:
        loader = TickDataLoader(symbols=["C:EURUSD", "X:BTCUSD"])
        async for tick in loader.stream():
            print(tick)
    """

    ASSET_WS = {
        "forex": "wss://socket.polygon.io/forex",
        "crypto": "wss://socket.polygon.io/crypto",
        "stocks": "wss://socket.polygon.io/stocks",
        "futures": "wss://socket.polygon.io/futures",
    }

    def __init__(
        self,
        symbols: list[str],
        api_key: Optional[str] = None,
        asset_type: str = "forex",
        symbol_map: dict[str, str] | None = None,
        poll_interval: float = DEFAULT_POLL_SECONDS,
    ):
        self.symbols = symbols
        self.api_key = api_key or os.getenv("POLYGON_API_KEY", "")
        self.asset_type = asset_type
        self.ws_url = self.ASSET_WS.get(asset_type, self.ASSET_WS["forex"])
        self._running = False
        self._symbol_map = symbol_map or {}
        self._poll_interval = max(0.25, poll_interval)
        self._last_prices: dict[str, float] = {}

    async def stream(self) -> AsyncGenerator[Tick, None]:
        """
        Async generator that yields Tick objects from the live stream.
        Reconnects automatically on disconnect.
        """
        self._running = True
        mode = os.getenv("TICK_STREAM_MODE", "websocket").strip().lower()
        if mode == "rest":
            async for tick in self._stream_rest():
                if not self._running:
                    break
                yield tick
            return

        try:
            import websockets
        except ImportError:
            raise ImportError("pip install websockets")

        reconnect_delay = 1

        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20) as ws:
                    reconnect_delay = 1

                    if not await self._authenticate_ws(ws):
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 30)
                        continue

                    channels = ",".join(self._subscribe_channel(sym) for sym in self.symbols)
                    await ws.send(json.dumps({"action": "subscribe", "params": channels}))
                    logger.info(
                        "TickDataLoader: subscribed to %d symbols on %s",
                        len(self.symbols),
                        self.asset_type,
                    )

                    async for raw in ws:
                        if not self._running:
                            break
                        payload = json.loads(raw)
                        for msg in payload if isinstance(payload, list) else [payload]:
                            tick = self._parse_message(msg)
                            if tick:
                                yield tick

            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    "TickDataLoader: disconnected (%s) — reconnecting in %ds",
                    e,
                    reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    async def stream_symbol(self, symbol: str) -> AsyncIterator[Tick]:
        """Yield ticks for one internal symbol (REST fallback when mode=rest)."""
        sym = symbol.upper()
        async for tick in self.stream():
            if tick.symbol == sym:
                yield tick

    def _subscribe_channel(self, symbol: str) -> str:
        """
        Polygon WebSocket channel names differ from REST tickers:
          forex  C.EUR/USD   (not C.EURUSD)
          crypto XT.BTC-USD  (not X.BTCUSD)
          stocks T.AAPL
          futures T.MES      (futures socket; not F.MES)
        """
        if "." in symbol and ":" not in symbol:
            return symbol
        if ":" in symbol:
            prefix, body = symbol.split(":", 1)
            pfx = prefix.upper()
            if pfx == "X":
                return f"XT.{_crypto_ws_pair(body)}"
            if pfx == "C" and self.asset_type == "forex":
                return f"C.{_forex_ws_pair(body)}"
            if pfx == "C" and self.asset_type == "futures":
                return f"T.{body.upper()}"
            return f"{prefix}.{body}"
        if self.asset_type == "crypto":
            return f"XT.{_crypto_ws_pair(symbol)}"
        if self.asset_type == "forex":
            return f"C.{_forex_ws_pair(symbol)}"
        if self.asset_type == "futures":
            return f"T.{symbol.upper()}"
        return f"T.{symbol.upper()}"

    def _resolve_symbol(self, raw: str) -> str:
        if not raw:
            return ""
        if raw in self._symbol_map:
            return self._symbol_map[raw]
        normalized = normalize_symbol(raw)
        if normalized in self._symbol_map.values():
            return normalized
        for polygon, internal in self._symbol_map.items():
            if normalize_symbol(polygon) == normalized or polygon == raw:
                return internal
        return normalized

    def _parse_message(self, msg: dict) -> Optional[Tick]:
        """Parse a Polygon WebSocket message into a Tick."""
        ev = msg.get("ev", "")

        if ev == "C":
            return Tick(
                symbol=self._resolve_symbol(str(msg.get("p", ""))),
                price=(float(msg.get("bp", 0)) + float(msg.get("ap", 0))) / 2,
                size=float(msg.get("as", 0) or msg.get("bs", 0) or 0),
                timestamp=msg.get("t", 0),
                bid=msg.get("bp"),
                ask=msg.get("ap"),
            )

        if ev == "XT":
            return Tick(
                symbol=self._resolve_symbol(str(msg.get("pair", ""))),
                price=msg.get("p", 0),
                size=msg.get("s", 0),
                timestamp=msg.get("t", 0),
            )

        if ev in ("T", "FT"):
            return Tick(
                symbol=self._resolve_symbol(str(msg.get("sym", ""))),
                price=msg.get("p", 0),
                size=msg.get("s", 0),
                timestamp=msg.get("t", 0),
            )

        return None

    async def _stream_rest(self) -> AsyncIterator[Tick]:
        """REST poll fallback for one-symbol-per-loader groups."""
        if not self.api_key:
            logger.warning("TickDataLoader: POLYGON_API_KEY not set — no ticks")
            return

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while self._running:
                for polygon_sym in self.symbols:
                    internal = self._resolve_symbol(polygon_sym)
                    url = f"https://api.polygon.io/v2/last/trade/{polygon_sym}"
                    try:
                        response = await client.get(url, params={"apiKey": self.api_key})
                        response.raise_for_status()
                        result = (response.json().get("results") or {})
                        price = float(result.get("p") or result.get("price") or 0)
                        if price <= 0:
                            continue
                        if self._last_prices.get(internal) == price:
                            continue
                        self._last_prices[internal] = price
                        yield Tick(
                            symbol=internal,
                            price=price,
                            size=float(result.get("s") or result.get("size") or 0),
                            timestamp=result.get("t") or result.get("sip_timestamp") or 0,
                        )
                    except httpx.HTTPError as exc:
                        logger.debug("TickDataLoader[%s] poll error: %s", internal, exc)
                await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _authenticate_ws(self, ws) -> bool:
        """
        Polygon sends `connected` before `auth_success` on separate messages.
        Read until auth succeeds/fails or times out.
        """
        if not self.api_key:
            logger.error("Polygon WebSocket auth failed: POLYGON_API_KEY not set")
            return False

        await ws.send(json.dumps({"action": "auth", "params": self.api_key}))

        for _ in range(10):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.error("Polygon WebSocket auth timed out waiting for auth_success")
                return False

            payload = json.loads(raw)
            messages = payload if isinstance(payload, list) else [payload]
            for msg in messages:
                status = msg.get("status")
                if status == "auth_success":
                    return True
                if status == "auth_failed":
                    logger.error("Polygon WebSocket auth failed: %s", msg)
                    return False

        logger.error("Polygon WebSocket auth failed: no auth_success after %d messages", 10)
        return False


def loaders_for_symbols(
    symbols: list[str],
    api_key: str | None = None,
    ticker_map: dict[str, str] | None = None,
) -> list[TickDataLoader]:
    """
    Build one WebSocket loader per asset class for the given internal symbols.
    """
    mapping = ticker_map or polygon_ticker_map()
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for sym in symbols:
        internal = sym.upper()
        polygon = mapping.get(internal)
        if not polygon:
            spec = get_symbol_or_none(internal)
            polygon = massive_symbol(internal) if spec else internal
        spec = get_symbol_or_none(internal)
        asset_type = _ASSET_CLASS_TO_WS.get(
            spec.asset_class if spec else "equity",
            "stocks",
        )
        groups[asset_type].append((internal, polygon))

    loaders: list[TickDataLoader] = []
    for asset_type, pairs in groups.items():
        symbol_map = {polygon: internal for internal, polygon in pairs}
        for internal, polygon in pairs:
            body = polygon.split(":", 1)[-1] if ":" in polygon else polygon
            symbol_map.setdefault(normalize_symbol(polygon), internal)
            symbol_map.setdefault(normalize_symbol(body), internal)
            if asset_type == "forex":
                symbol_map.setdefault(_forex_ws_pair(body), internal)
            elif asset_type == "crypto":
                symbol_map.setdefault(_crypto_ws_pair(body), internal)
            elif "/" in polygon:
                symbol_map.setdefault(polygon, internal)
        loaders.append(
            TickDataLoader(
                symbols=[polygon for _, polygon in pairs],
                api_key=api_key,
                asset_type=asset_type,
                symbol_map=symbol_map,
            )
        )
    return loaders
