"""
live/broker_adapter.py

Market-data broker adapters for WATCHER_MODE=live / --mode worker.
Execution adapters (Tradovate, IBKR) are future work — this module feeds OHLCV bars only.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

import httpx

from pipeline.schemas import OHLCV

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 15.0

# Override per symbol via POLYGON_TICKER_MES=... or POLYGON_FUTURES_TICKER_MAP JSON
DEFAULT_POLYGON_TICKERS: dict[str, str] = {
    "MES": "C:MES",
    "ES": "C:ES",
    "NQ": "C:NQ",
    "MNQ": "C:MNQ",
    "CL": "C:CL",
    "GC": "C:GC",
    "ZB": "C:ZB",
    "RTY": "C:RTY",
    "YM": "C:YM",
}


def _load_polygon_ticker_map() -> dict[str, str]:
    mapping = dict(DEFAULT_POLYGON_TICKERS)
    raw = os.getenv("POLYGON_FUTURES_TICKER_MAP", "").strip()
    if raw:
        try:
            mapping.update({k.upper(): v for k, v in json.loads(raw).items()})
        except json.JSONDecodeError:
            logger.warning("Invalid POLYGON_FUTURES_TICKER_MAP JSON — using defaults")
    for key, value in os.environ.items():
        if key.startswith("POLYGON_TICKER_"):
            sym = key.removeprefix("POLYGON_TICKER_").upper()
            if sym and value.strip():
                mapping[sym] = value.strip()
    return mapping


class BrokerAdapter(ABC):
    """Fetch latest completed OHLCV bar for chart watcher live mode."""

    broker_id: str = "base"

    @abstractmethod
    async def fetch_latest_bar(self, symbol: str, timeframe: str = "1m") -> Optional[OHLCV]:
        ...


class NullBrokerAdapter(BrokerAdapter):
    broker_id = "none"

    async def fetch_latest_bar(self, symbol: str, timeframe: str = "1m") -> Optional[OHLCV]:
        return None


class PaperBrokerAdapter(BrokerAdapter):
    """Paper mode in live loop — no external market feed."""

    broker_id = "paper"

    async def fetch_latest_bar(self, symbol: str, timeframe: str = "1m") -> Optional[OHLCV]:
        return None


class PolygonBrokerAdapter(BrokerAdapter):
    """Polygon.io aggregates — previous 1m bar (works on developer plans)."""

    broker_id = "polygon"

    def __init__(self, api_key: str | None = None, ticker_map: dict[str, str] | None = None) -> None:
        self._api_key = api_key or os.getenv("POLYGON_API_KEY", "")
        self._ticker_map = ticker_map or _load_polygon_ticker_map()

    def resolve_ticker(self, symbol: str) -> str:
        sym = symbol.upper()
        return self._ticker_map.get(sym, f"C:{sym}")

    async def fetch_latest_bar(self, symbol: str, timeframe: str = "1m") -> Optional[OHLCV]:
        if timeframe != "1m":
            logger.debug("PolygonBrokerAdapter: only 1m supported in v1 (got %s)", timeframe)
        if not self._api_key:
            logger.warning("PolygonBrokerAdapter: POLYGON_API_KEY not set")
            return None

        ticker = self.resolve_ticker(symbol)
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev"
        params = {"adjusted": "true", "apiKey": self._api_key}

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("PolygonBrokerAdapter[%s] HTTP error: %s", symbol, exc)
            return None

        results = payload.get("results") or []
        if not results:
            logger.debug("PolygonBrokerAdapter[%s]: no prev bar (ticker=%s)", symbol, ticker)
            return None

        row = results[0]
        ts_ms = row.get("T") or row.get("t")
        if ts_ms is None:
            return None

        ts = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
        return OHLCV(
            symbol=symbol.upper(),
            timeframe="1m",
            timestamp=ts,
            open=float(row.get("o", 0)),
            high=float(row.get("h", 0)),
            low=float(row.get("l", 0)),
            close=float(row.get("c", 0)),
            volume=float(row.get("v", 0)),
        )


_ADAPTERS: dict[str, type[BrokerAdapter]] = {
    "none": NullBrokerAdapter,
    "paper": PaperBrokerAdapter,
    "polygon": PolygonBrokerAdapter,
}


def register_broker_adapter(broker_id: str, adapter_cls: type[BrokerAdapter]) -> None:
    _ADAPTERS[broker_id.lower()] = adapter_cls


def get_broker_adapter(broker: str) -> BrokerAdapter:
    broker_id = (broker or "none").lower()
    cls = _ADAPTERS.get(broker_id, NullBrokerAdapter)
    return cls()


def default_worker_broker() -> str:
    """Pick market-data broker for worker/live when BROKER is unset."""
    explicit = os.getenv("BROKER", "").strip().lower()
    if explicit and explicit not in ("none", ""):
        return explicit
    if os.getenv("POLYGON_API_KEY"):
        return "polygon"
    if os.getenv("PAPER_TRADING_ENABLED", "true").lower() == "true":
        return "paper"
    return "none"
