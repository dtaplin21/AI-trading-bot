"""Tests for Polygon historical backfill helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.providers.polygon_backfill import (
    PolygonBackfillClient,
    empty_agg_data_advice,
    export_ohlcv_csv,
    format_polygon_agg_hint,
    iter_date_chunks,
    parse_date,
    parse_timeframe,
)


def test_format_polygon_agg_hint_includes_status_and_message():
    hint = format_polygon_agg_hint(
        {
            "status": "ERROR",
            "resultsCount": 0,
            "message": "Ticker not entitled",
            "request_id": "abc",
        },
        http_status=200,
    )
    assert "status='ERROR'" in hint
    assert "resultsCount=0" in hint
    assert "Ticker not entitled" in hint


def test_empty_agg_data_advice_detects_entitlement():
    advice = empty_agg_data_advice("OK", "Your plan does not have access to this data")
    assert "plan" in advice.lower()


def test_parse_timeframe():
    assert parse_timeframe("1m") == (1, "minute")
    assert parse_timeframe("1h") == (1, "hour")


def test_parse_date():
    dt = parse_date("2025-06-01")
    assert dt.year == 2025
    assert dt.tzinfo == timezone.utc


def test_iter_date_chunks():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 2, 15, tzinfo=timezone.utc)
    chunks = list(iter_date_chunks(start, end, chunk_days=30))
    assert len(chunks) >= 2
    assert chunks[0][0] == start


def test_fetch_range_pagination(tmp_path):
    payload_page1 = {
        "status": "OK",
        "results": [
            {"t": 1704067200000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100},
        ],
        "next_url": "https://api.polygon.io/v2/aggs/ticker/C:MES/range/1/minute/0/1?cursor=abc",
    }
    payload_page2 = {
        "status": "OK",
        "results": [
            {"t": 1704067260000, "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 110},
        ],
    }

    client = PolygonBackfillClient(api_key="test-key", request_delay=0)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    def fake_get(url, params=None):
        if "cursor" in url:
            mock_response.json.return_value = payload_page2
        else:
            mock_response.json.return_value = payload_page1
        return mock_response

    with patch("data.providers.polygon_backfill.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.side_effect = fake_get
        mock_client_cls.return_value = mock_client

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        with patch.object(client, "_resolver") as mock_res:
            mock_res.resolve_ticker.return_value = "C:MES"
            df = client._fetch_chunk("C:MES", "MES", 1, "minute", start, end)

    assert len(df) == 2
    assert "close" in df.columns


def test_fetch_chunk_retries_429_with_api_key():
    """After 429, retry must still send apiKey (regression: bare URL caused 401)."""
    payload_ok = {
        "status": "OK",
        "results": [{"t": 1704067200000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}],
    }
    client = PolygonBackfillClient(api_key="test-key", request_delay=0, rate_limit_sleep=0)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    calls: list[tuple[str | None, dict | None]] = []

    def fake_get(url, params=None):
        calls.append((url, params))
        if len(calls) == 1:
            mock_response.status_code = 429
            mock_response.json.return_value = {}
            return mock_response
        mock_response.status_code = 200
        mock_response.json.return_value = payload_ok
        return mock_response

    with patch("data.providers.polygon_backfill.httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_http.__enter__.return_value = mock_http
        mock_http.get.side_effect = fake_get
        mock_client_cls.return_value = mock_http

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        df = client._fetch_chunk("C:MES", "MES", 1, "minute", start, end)

    assert len(df) == 1
    assert client.last_chunk_diagnostic == ""
    assert len(calls) == 2
    assert calls[0][1] is not None and calls[0][1].get("apiKey") == "test-key"
    assert calls[1][1] is not None and calls[1][1].get("apiKey") == "test-key"


def test_fetch_chunk_sets_diagnostic_when_empty():
    client = PolygonBackfillClient(api_key="test-key", request_delay=0)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "status": "OK",
        "resultsCount": 0,
        "results": [],
        "message": "No data found for this range",
    }

    with patch("data.providers.polygon_backfill.httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_http.__enter__.return_value = mock_http
        mock_http.get.return_value = mock_response
        mock_client_cls.return_value = mock_http

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 30, tzinfo=timezone.utc)
        df = client._fetch_chunk("C:MES", "MES", 1, "minute", start, end)

    assert df.empty
    assert "resultsCount=0" in client.last_chunk_diagnostic
    assert "No data found" in client.last_chunk_diagnostic


def test_export_ohlcv_csv(tmp_path):
    idx = pd.date_range("2025-01-01", periods=2, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {"open": [1, 2], "high": [2, 3], "low": [0.5, 1], "close": [1.5, 2.5], "volume": [10, 20]},
        index=idx,
    )
    path = tmp_path / "MES_1m.csv"
    export_ohlcv_csv(df, str(path))
    text = path.read_text()
    assert "timestamp" in text
    assert "open" in text
