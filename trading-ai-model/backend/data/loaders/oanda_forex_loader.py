"""
data/loaders/oanda_forex_loader.py

Forex tick stream via OANDA v20 — avoids Polygon forex (zero-close poison).

Modes (OANDA_PRICING_STREAM=true + OANDA_ACCOUNT_ID):
  HTTP pricing stream — sub-second mid updates

Default:
  Poll instrument candles every OANDA_PRICING_POLL_SEC (uses forming M1 mid)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import AsyncGenerator, AsyncIterator

import httpx

from config.oanda_symbols import is_oanda_tradable, to_instrument
from data.loaders.tick_data_loader import Tick
from live.broker_adapter import HTTP_TIMEOUT, oanda_api_base, oanda_api_key, parse_oanda_candle

logger = logging.getLogger(__name__)

DEFAULT_POLL_SEC = float(os.getenv("OANDA_PRICING_POLL_SEC", "2.0"))


def oanda_pricing_stream_enabled() -> bool:
    return os.getenv("OANDA_PRICING_STREAM", "false").lower() in ("true", "1", "yes")


def oanda_account_id() -> str:
    return (os.getenv("OANDA_ACCOUNT_ID") or "").strip()


class OandaForexTickLoader:
    """Yield Tick objects for OANDA forex pairs (never Polygon)."""

    def __init__(
        self,
        symbols: list[str],
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        account_id: str | None = None,
        poll_interval: float = DEFAULT_POLL_SEC,
    ) -> None:
        self.symbols = [s.upper() for s in symbols if is_oanda_tradable(s)]
        self._api_key = api_key if api_key is not None else oanda_api_key()
        self._api_base = (api_base or oanda_api_base()).rstrip("/")
        self._account_id = account_id if account_id is not None else oanda_account_id()
        self._poll_interval = max(0.5, poll_interval)
        self._running = False
        self._last_prices: dict[str, float] = {}

    def _instruments_csv(self) -> str:
        parts = []
        for sym in self.symbols:
            inst = to_instrument(sym)
            if inst:
                parts.append(inst)
        return ",".join(parts)

    async def stream(self) -> AsyncGenerator[Tick, None]:
        self._running = True
        if (
            oanda_pricing_stream_enabled()
            and self._account_id
            and self._instruments_csv()
        ):
            async for tick in self._stream_pricing():
                if not self._running:
                    break
                yield tick
            return

        async for tick in self._poll_candles():
            if not self._running:
                break
            yield tick

    async def _poll_candles(self) -> AsyncIterator[Tick]:
        if not self._api_key:
            logger.warning("OandaForexTickLoader: OANDA_API_KEY not set")
            return

        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while self._running:
                for sym in self.symbols:
                    instrument = to_instrument(sym)
                    if instrument is None:
                        continue
                    url = f"{self._api_base}/v3/instruments/{instrument}/candles"
                    params = {"granularity": "M1", "count": "2", "price": "M"}
                    try:
                        response = await client.get(url, params=params, headers=headers)
                        response.raise_for_status()
                        candles = (response.json().get("candles") or [])
                    except httpx.HTTPError as exc:
                        logger.debug("OandaForexTickLoader[%s] poll error: %s", sym, exc)
                        continue

                    for candle in reversed(candles):
                        bar = parse_oanda_candle(
                            sym, candle, timeframe="1m", allow_incomplete=True
                        )
                        if bar is None:
                            continue
                        if self._last_prices.get(sym) == bar.close:
                            break
                        self._last_prices[sym] = bar.close
                        yield Tick(
                            symbol=sym,
                            price=bar.close,
                            size=0.0,
                            timestamp=bar.timestamp,
                        )
                        break

                await asyncio.sleep(self._poll_interval)

    async def _stream_pricing(self) -> AsyncIterator[Tick]:
        instruments = self._instruments_csv()
        if not self._api_key or not self._account_id:
            return

        url = f"{self._api_base}/v3/accounts/{self._account_id}/pricing/stream"
        params = {"instruments": instruments}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        logger.info(
            "OandaForexTickLoader: pricing stream | account=%s instruments=%s",
            self._account_id,
            instruments,
        )

        reconnect_delay = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "GET", url, params=params, headers=headers
                    ) as response:
                        response.raise_for_status()
                        reconnect_delay = 1.0
                        async for line in response.aiter_lines():
                            if not self._running:
                                return
                            line = (line or "").strip()
                            if not line:
                                continue
                            tick = self._parse_pricing_line(line)
                            if tick is not None:
                                sym = tick.symbol.upper()
                                if self._last_prices.get(sym) == tick.price:
                                    continue
                                self._last_prices[sym] = tick.price
                                yield tick
            except httpx.HTTPError as exc:
                logger.warning("OandaForexTickLoader stream error: %s — reconnect", exc)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)

    def _parse_pricing_line(self, line: str) -> Tick | None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return None

        if msg.get("type") != "PRICE":
            return None

        instrument = str(msg.get("instrument") or "")
        sym = instrument.replace("_", "")
        if not is_oanda_tradable(sym):
            return None

        bids = msg.get("bids") or []
        asks = msg.get("asks") or []
        bid = float(bids[0]["price"]) if bids else 0.0
        ask = float(asks[0]["price"]) if asks else 0.0
        if bid > 0 and ask > 0:
            price = (bid + ask) / 2.0
        elif bid > 0:
            price = bid
        elif ask > 0:
            price = ask
        else:
            close = msg.get("closeoutMid") or msg.get("closeoutAsk") or msg.get("closeoutBid")
            price = float(close) if close else 0.0

        if price <= 0:
            return None

        ts_raw = msg.get("time")
        timestamp: object = ts_raw or 0
        if isinstance(ts_raw, str) and ts_raw.strip():
            try:
                timestamp = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                timestamp = ts_raw

        return Tick(symbol=sym, price=price, size=0.0, timestamp=timestamp)

    def stop(self) -> None:
        self._running = False
