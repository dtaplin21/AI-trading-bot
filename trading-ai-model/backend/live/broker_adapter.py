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

from config.oanda_symbols import is_oanda_tradable, to_instrument
from config.symbols import get_symbol_or_none, massive_symbol, polygon_ticker_map
from pipeline.bar_validators import is_valid_bar_close
from pipeline.schemas import OHLCV

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 15.0

_OANDA_GRANULARITY = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1"}


def oanda_api_key() -> str:
    return (os.getenv("OANDA_API_KEY") or os.getenv("ONDA_API_KEY") or "").strip()


def oanda_api_base() -> str:
    env = os.getenv("OANDA_ENVIRONMENT", "").strip().lower()
    if not env:
        practice = os.getenv("OANDA_PRACTICE", "true").lower() in ("true", "1", "yes")
        env = "practice" if practice else "live"
    if env == "live":
        return "https://api-fxtrade.oanda.com"
    return "https://api-fxpractice.oanda.com"


def parse_oanda_candle(symbol: str, candle: dict, *, timeframe: str = "1m") -> Optional[OHLCV]:
    """Parse one OANDA v20 candle dict into OHLCV (mid price)."""
    if not candle.get("complete", True):
        return None
    mid = candle.get("mid") or candle.get("bid") or candle.get("ask")
    if not mid:
        return None
    close = float(mid.get("c", 0))
    if not is_valid_bar_close(close):
        return None
    time_str = str(candle.get("time") or "")
    if not time_str:
        return None
    ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    open_ = float(mid.get("o", close))
    high = float(mid.get("h", close))
    low = float(mid.get("l", close))
    return OHLCV(
        symbol=symbol.upper(),
        timeframe=timeframe,
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=float(candle.get("volume", 0)),
    )


def _load_polygon_ticker_map() -> dict[str, str]:
    mapping = polygon_ticker_map()
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
        if sym in self._ticker_map:
            return self._ticker_map[sym]
        if get_symbol_or_none(sym):
            return massive_symbol(sym)
        return f"C:{sym}"

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
        close = float(row.get("c", 0))
        if not is_valid_bar_close(close):
            logger.warning(
                "PolygonBrokerAdapter[%s]: rejecting invalid close %.6f (ticker=%s)",
                symbol,
                close,
                ticker,
            )
            return None
        return OHLCV(
            symbol=symbol.upper(),
            timeframe="1m",
            timestamp=ts,
            open=float(row.get("o", close)),
            high=float(row.get("h", close)),
            low=float(row.get("l", close)),
            close=close,
            volume=float(row.get("v", 0)),
        )


class OandaBrokerAdapter(BrokerAdapter):
    """OANDA v20 instrument candles — forex M1 bars (no account id required)."""

    broker_id = "oanda"

    def __init__(self, api_key: str | None = None, api_base: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else oanda_api_key()
        self._api_base = (api_base or oanda_api_base()).rstrip("/")

    async def fetch_latest_bar(self, symbol: str, timeframe: str = "1m") -> Optional[OHLCV]:
        instrument = to_instrument(symbol)
        if instrument is None:
            logger.debug("OandaBrokerAdapter[%s]: not a tradable forex pair", symbol)
            return None
        if not self._api_key:
            logger.warning("OandaBrokerAdapter: OANDA_API_KEY not set")
            return None

        granularity = _OANDA_GRANULARITY.get(timeframe, "M1")
        url = f"{self._api_base}/v3/instruments/{instrument}/candles"
        params = {"granularity": granularity, "count": "3", "price": "M"}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("OandaBrokerAdapter[%s] HTTP error: %s", symbol, exc)
            return None

        candles = payload.get("candles") or []
        for candle in reversed(candles):
            bar = parse_oanda_candle(symbol, candle, timeframe=timeframe)
            if bar is not None:
                return bar

        logger.debug("OandaBrokerAdapter[%s]: no complete candle (instrument=%s)", symbol, instrument)
        return None


async def fetch_latest_bar_for_symbol(
    symbol: str,
    *,
    broker: str = "polygon",
    timeframe: str = "1m",
    oanda_adapter: OandaBrokerAdapter | None = None,
    primary_adapter: BrokerAdapter | None = None,
) -> Optional[OHLCV]:
    """
    Resolve market-data source per symbol: forex → OANDA when creds exist,
    otherwise fall back to the configured primary broker (usually Polygon).
    """
    from config.execution_config import oanda_credentials_ready
    from config.settings import get_settings

    sym = symbol.upper()

    if is_oanda_tradable(sym) and oanda_credentials_ready(get_settings()):
        oanda = oanda_adapter or OandaBrokerAdapter()
        bar = await oanda.fetch_latest_bar(sym, timeframe)
        if bar is not None:
            return bar
        logger.warning(
            "OandaBrokerAdapter[%s]: no valid bar — falling back to broker=%s",
            sym,
            broker,
        )

    primary = primary_adapter or get_broker_adapter(broker)
    if primary.broker_id == "oanda":
        return None
    return await primary.fetch_latest_bar(sym, timeframe)


_ADAPTERS: dict[str, type[BrokerAdapter]] = {
    "none": NullBrokerAdapter,
    "paper": PaperBrokerAdapter,
    "polygon": PolygonBrokerAdapter,
    "oanda": OandaBrokerAdapter,
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
