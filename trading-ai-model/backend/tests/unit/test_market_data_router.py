"""Tests for unified market-data routing."""

from __future__ import annotations

import pytest

from live.broker_adapter import CoinbaseBrokerAdapter, OandaBrokerAdapter, PolygonBrokerAdapter
from live.market_data_router import (
    clear_market_data_adapter_cache,
    resolve_market_data_adapter,
    resolve_market_data_broker_id,
)


@pytest.fixture(autouse=True)
def _clear_adapter_cache():
    clear_market_data_adapter_cache()
    yield
    clear_market_data_adapter_cache()


def test_resolve_btcusd_to_coinbase(monkeypatch):
    monkeypatch.setenv("COINBASE_API_KEY", "organizations/test/key")
    monkeypatch.setenv("COINBASE_API_SECRET", "secret")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    assert resolve_market_data_broker_id("BTCUSD") == "coinbase"
    adapter = resolve_market_data_adapter("BTCUSD")
    assert isinstance(adapter, CoinbaseBrokerAdapter)
    assert adapter.broker_id == "coinbase"


def test_resolve_eurusd_to_oanda(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-token")
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    assert resolve_market_data_broker_id("EURUSD") == "oanda"
    adapter = resolve_market_data_adapter("EURUSD")
    assert isinstance(adapter, OandaBrokerAdapter)


def test_resolve_mes_to_polygon(monkeypatch):
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    assert resolve_market_data_broker_id("MES") == "polygon"
    adapter = resolve_market_data_adapter("MES")
    assert isinstance(adapter, PolygonBrokerAdapter)


def test_resolve_mes_none_without_polygon_key(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_API_KEY", raising=False)

    assert resolve_market_data_broker_id("MES") == "none"
    assert resolve_market_data_adapter("MES").broker_id == "none"


def test_crypto_prefers_coinbase_over_polygon(monkeypatch):
    monkeypatch.setenv("COINBASE_API_KEY", "organizations/test/key")
    monkeypatch.setenv("COINBASE_API_SECRET", "secret")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    assert resolve_market_data_broker_id("ETHUSD") == "coinbase"


def test_forex_prefers_oanda_over_polygon(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-token")
    monkeypatch.setenv("POLYGON_API_KEY", "pk_test")

    assert resolve_market_data_broker_id("GBPUSD") == "oanda"
