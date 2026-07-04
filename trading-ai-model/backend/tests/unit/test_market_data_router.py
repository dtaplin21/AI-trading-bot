"""Parametrized routing matrix for MARKET_DATA_PRIMARY."""

from __future__ import annotations

import pytest

from config.settings import Settings
from live.market_data_router import (
    clear_market_data_adapter_cache,
    resolve_market_data_broker_id,
)


@pytest.fixture(autouse=True)
def _clear_adapter_cache():
    clear_market_data_adapter_cache()
    yield
    clear_market_data_adapter_cache()


def _settings(
    *,
    coinbase_key: str = "",
    coinbase_secret: str = "",
    oanda_key: str = "",
    polygon_key: str = "",
    primary: str = "coinbase,oanda,polygon",
) -> Settings:
    return Settings.model_construct(
        coinbase_api_key=coinbase_key,
        coinbase_api_secret=coinbase_secret,
        oanda_api_key=oanda_key,
        polygon_api_key=polygon_key,
        market_data_primary=primary,
    )


ROUTING_MATRIX = [
    # symbol, creds, primary, expected
    ("BTCUSD", {"cb_k": "k", "cb_s": "s", "poly": "pk"}, "coinbase,oanda,polygon", "coinbase"),
    ("ETHUSD", {"cb_k": "k", "cb_s": "s", "poly": "pk"}, "coinbase,oanda,polygon", "coinbase"),
    ("EURUSD", {"oanda": "tok", "poly": "pk"}, "coinbase,oanda,polygon", "oanda"),
    ("GBPUSD", {"oanda": "tok", "poly": "pk"}, "coinbase,oanda,polygon", "oanda"),
    ("MES", {"poly": "pk"}, "coinbase,oanda,polygon", "polygon"),
    ("TSLA", {"poly": "pk"}, "coinbase,oanda,polygon", "polygon"),
    ("BTCUSD", {"poly": "pk"}, "coinbase,oanda,polygon", "none"),
    ("EURUSD", {"poly": "pk"}, "coinbase,oanda,polygon", "none"),
    ("BTCUSD", {"poly": "pk"}, "polygon", "polygon"),
    ("EURUSD", {"poly": "pk"}, "polygon", "polygon"),
    ("MES", {}, "coinbase,oanda,polygon", "none"),
    ("BTCUSD", {"cb_k": "k", "cb_s": "s", "oanda": "tok", "poly": "pk"}, "polygon,coinbase,oanda", "polygon"),
    ("EURUSD", {"cb_k": "k", "cb_s": "s", "oanda": "tok", "poly": "pk"}, "polygon,oanda,coinbase", "polygon"),
]


@pytest.mark.parametrize(
    "symbol,creds,primary,expected",
    ROUTING_MATRIX,
    ids=[f"{row[0]}-{row[3]}" for row in ROUTING_MATRIX],
)
def test_routing_matrix(symbol, creds, primary, expected):
    settings = _settings(
        coinbase_key=creds.get("cb_k", ""),
        coinbase_secret=creds.get("cb_s", ""),
        oanda_key=creds.get("oanda", ""),
        polygon_key=creds.get("poly", ""),
        primary=primary,
    )
    assert resolve_market_data_broker_id(symbol, settings=settings) == expected


def test_build_market_data_feed_summary_label(monkeypatch):
    from live.market_data_router import build_market_data_feed_summary

    monkeypatch.setenv("TICK_STREAM_MODE", "websocket")
    settings = _settings(
        coinbase_key="k",
        coinbase_secret="s",
        oanda_key="tok",
        polygon_key="pk",
    )
    summary = build_market_data_feed_summary(settings)
    assert summary["tick_stream_mode"] == "websocket"
    assert summary["by_asset_class"]["crypto"] == "coinbase"
    assert summary["by_asset_class"]["forex"] == "oanda"
    assert summary["by_asset_class"]["futures"] == "polygon"
    assert "Coinbase" in summary["label"]
    assert "OANDA" in summary["label"]
    assert "Polygon" in summary["label"]
