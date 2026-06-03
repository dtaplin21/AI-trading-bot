"""
Polygon.io historical aggregate backfill for TimescaleDB / CSV replay files.

Uses: GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import httpx
import pandas as pd

from live.broker_adapter import PolygonBrokerAdapter

logger = logging.getLogger(__name__)

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

HTTP_TIMEOUT = 60.0
DEFAULT_CHUNK_DAYS = 30
DEFAULT_REQUEST_DELAY = 0.25
DEFAULT_RATE_LIMIT_SLEEP = float(os.getenv("BACKFILL_RATE_SLEEP", "65"))

# Internal timeframe → (multiplier, polygon timespan)
TIMEFRAME_SPEC: dict[str, tuple[int, str]] = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
}


def parse_timeframe(timeframe: str) -> tuple[int, str]:
    tf = timeframe.strip().lower()
    if tf not in TIMEFRAME_SPEC:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use one of {list(TIMEFRAME_SPEC)}")
    return TIMEFRAME_SPEC[tf]


def parse_date(s: str) -> datetime:
    """Parse YYYY-MM-DD or ISO datetime to UTC."""
    s = s.strip()
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iter_date_chunks(
    start: datetime,
    end: datetime,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
) -> Iterator[tuple[datetime, datetime]]:
    """Yield [chunk_start, chunk_end] inclusive date windows."""
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days) - timedelta(seconds=1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(seconds=1)


def _ms(dt: datetime) -> int:
    t = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return int(t.timestamp() * 1000)


def _empty_ohlcv_df() -> pd.DataFrame:
    """Typed empty OHLCV frame (avoids pandas stub issues with columns=[...])."""
    return pd.DataFrame({col: pd.Series(dtype=float) for col in OHLCV_COLUMNS})


def _select_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return OHLCV columns only; explicit copy for static type checkers."""
    return df.loc[:, list(OHLCV_COLUMNS)].copy()


def format_polygon_agg_hint(payload: dict, *, http_status: int = 200) -> str:
    """Human-readable summary of a Polygon aggregates JSON body (for empty/error debugging)."""
    parts: list[str] = [f"http={http_status}"]
    status = payload.get("status")
    if status is not None:
        parts.append(f"status={status!r}")
    for key in ("resultsCount", "queryCount", "count", "request_id"):
        if key in payload and payload[key] is not None:
            parts.append(f"{key}={payload[key]!r}")
    for key in ("message", "error"):
        text = payload.get(key)
        if text:
            parts.append(f"{key}={text!r}")
    if len(parts) == 1:
        parts.append("no status/message fields in JSON body")
    return " | ".join(parts)


def empty_agg_data_advice(polygon_status: str | None, message: str | None) -> str:
    """Short hint when results[] is empty but HTTP succeeded."""
    msg = (message or "").lower()
    if polygon_status and polygon_status not in ("OK", "DELAYED"):
        return "Polygon returned a non-OK status — check API plan and ticker."
    if "not entitled" in msg or "does not have access" in msg or "subscription" in msg:
        return "Your API key may not include this asset class or timeframe on your plan."
    if "ticker" in msg and ("not found" in msg or "unknown" in msg or "invalid" in msg):
        return "Ticker may be wrong — futures often need a contract code, not C:SYMBOL."
    return (
        "Common causes: wrong futures ticker (C:MES vs contract), plan excludes "
        "futures 1m history, or no trading in that date range."
    )


def _agg_row_to_series(ticker_symbol: str, row: dict) -> dict | None:
    ts_ms = row.get("t") or row.get("T")
    if ts_ms is None:
        return None
    ts = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
    return {
        "time": ts,
        "open": float(row.get("o", 0)),
        "high": float(row.get("h", 0)),
        "low": float(row.get("l", 0)),
        "close": float(row.get("c", 0)),
        "volume": float(row.get("v", 0)),
    }


class PolygonBackfillClient:
    """Fetch historical OHLCV from Polygon and return DataFrames."""

    def __init__(
        self,
        api_key: str | None = None,
        ticker_resolver: PolygonBrokerAdapter | None = None,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        rate_limit_sleep: float = DEFAULT_RATE_LIMIT_SLEEP,
    ) -> None:
        if api_key:
            self._api_key = api_key
        else:
            from config.env_resolve import resolve_env

            self._api_key = resolve_env("POLYGON_API_KEY")
        self._resolver = ticker_resolver or PolygonBrokerAdapter(api_key=self._api_key)
        self._delay = request_delay
        self._rate_limit_sleep = rate_limit_sleep
        # Set when the latest fetch_chunk returned no rows (for backfill script logs).
        self.last_chunk_diagnostic: str = ""

    def resolve_ticker(self, symbol: str) -> str:
        return self._resolver.resolve_ticker(symbol)

    def fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        chunk_days: int = DEFAULT_CHUNK_DAYS,
    ) -> pd.DataFrame:
        if not self._api_key:
            raise RuntimeError("POLYGON_API_KEY is not set")
        from config.env_resolve import is_env_placeholder

        if is_env_placeholder(self._api_key):
            raise RuntimeError(
                "POLYGON_API_KEY is a placeholder (<your key>). "
                "Run: unset POLYGON_API_KEY  (then use backend/.env)"
            )

        multiplier, timespan = parse_timeframe(timeframe)
        ticker = self.resolve_ticker(symbol)
        frames: list[pd.DataFrame] = []

        for chunk_start, chunk_end in iter_date_chunks(start, end, chunk_days):
            df = self.fetch_chunk(
                symbol, timeframe, chunk_start, chunk_end, ticker=ticker
            )
            if not df.empty:
                frames.append(df)
                logger.info(
                    "PolygonBackfill[%s]: chunk %s → %s | %d bars (ticker=%s)",
                    symbol,
                    chunk_start.date(),
                    chunk_end.date(),
                    len(df),
                    ticker,
                )
            if self._delay > 0:
                time.sleep(self._delay)

        if not frames:
            return _empty_ohlcv_df()

        merged = pd.concat(frames).sort_index()
        deduped = merged.loc[~merged.index.duplicated(keep="last")]
        return _select_ohlcv_columns(deduped)

    def fetch_chunk(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        """Fetch one date window (used by resumable backfill script)."""
        multiplier, timespan = parse_timeframe(timeframe)
        resolved = ticker or self.resolve_ticker(symbol)
        return self._fetch_chunk(resolved, symbol, multiplier, timespan, start, end)

    def _fetch_chunk(
        self,
        ticker: str,
        symbol: str,
        multiplier: int,
        timespan: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        from_ms = _ms(start)
        to_ms = _ms(end)
        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/"
            f"{multiplier}/{timespan}/{from_ms}/{to_ms}"
        )
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": self._api_key,
        }
        rows: list[dict] = []
        self.last_chunk_diagnostic = ""
        sample_empty_payload: dict | None = None
        sample_http_status = 200

        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            next_url: str | None = url
            use_query_params = True
            while next_url:
                if use_query_params:
                    response = client.get(next_url, params=params)
                else:
                    response = client.get(self._ensure_api_key(next_url))
                if response.status_code == 429:
                    logger.warning(
                        "PolygonBackfill[%s]: rate limited — sleeping %.0fs",
                        symbol,
                        self._rate_limit_sleep,
                    )
                    time.sleep(self._rate_limit_sleep)
                    continue
                if response.status_code == 401 and use_query_params:
                    logger.warning(
                        "PolygonBackfill[%s]: 401 on paginated URL — retrying with apiKey",
                        symbol,
                    )
                    use_query_params = False
                    next_url = self._ensure_api_key(next_url)
                    time.sleep(2)
                    continue
                if response.status_code == 403:
                    logger.error(
                        "PolygonBackfill[%s]: forbidden (ticker=%s) — check plan/ticker",
                        symbol,
                        ticker,
                    )
                    break
                response.raise_for_status()
                payload = response.json()
                status = payload.get("status")
                page_results = payload.get("results") or []
                if not page_results and sample_empty_payload is None:
                    sample_empty_payload = payload
                    sample_http_status = response.status_code
                if status not in ("OK", "DELAYED", None):
                    logger.warning(
                        "PolygonBackfill[%s]: %s (ticker=%s, %s → %s)",
                        symbol,
                        format_polygon_agg_hint(payload, http_status=response.status_code),
                        ticker,
                        start.date(),
                        end.date(),
                    )
                for row in page_results:
                    parsed = _agg_row_to_series(symbol, row)
                    if parsed:
                        rows.append(parsed)
                page_next = payload.get("next_url")
                if page_next:
                    next_url = self._ensure_api_key(page_next)
                    use_query_params = False
                    if self._delay > 0:
                        time.sleep(self._delay)
                else:
                    next_url = None

        if not rows:
            if sample_empty_payload is not None:
                hint = format_polygon_agg_hint(
                    sample_empty_payload, http_status=sample_http_status
                )
                advice = empty_agg_data_advice(
                    sample_empty_payload.get("status"),
                    sample_empty_payload.get("message")
                    or sample_empty_payload.get("error"),
                )
                self.last_chunk_diagnostic = f"{hint} — {advice}"
                logger.warning(
                    "PolygonBackfill[%s]: empty chunk ticker=%s %s → %s | %s",
                    symbol,
                    ticker,
                    start.date(),
                    end.date(),
                    self.last_chunk_diagnostic,
                )
            else:
                self.last_chunk_diagnostic = "HTTP OK but no aggregate pages returned"
                logger.warning(
                    "PolygonBackfill[%s]: empty chunk ticker=%s %s → %s (no response body)",
                    symbol,
                    ticker,
                    start.date(),
                    end.date(),
                )
            return _empty_ohlcv_df()

        df = pd.DataFrame(rows).set_index("time").sort_index()
        return _select_ohlcv_columns(df)

    def _ensure_api_key(self, next_url: str) -> str:
        parsed = urlparse(next_url)
        qs = parse_qs(parsed.query)
        if "apiKey" not in qs and self._api_key:
            qs["apiKey"] = [self._api_key]
            query = urlencode({k: v[0] for k, v in qs.items()})
            return urlunparse(parsed._replace(query=query))
        return next_url


def export_ohlcv_csv(df: pd.DataFrame, path: str, *, append: bool = False) -> None:
    """Write replay-compatible CSV (timestamp, ohlcv columns)."""
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    export = df.reset_index()
    ts_col = str(export.columns[0])
    if ts_col != "timestamp":
        export = export.rename(columns={ts_col: "timestamp"})
    export["timestamp"] = pd.to_datetime(export["timestamp"], utc=True).dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    write_header = not (append and out.exists())
    export[cols].to_csv(out, mode="a" if append and out.exists() else "w", header=write_header, index=False)
    logger.info("Wrote %d rows to %s", len(export), out)
