"""Tests for cross-process order sizing runtime."""

from __future__ import annotations

import pytest

import risk.order_sizing_runtime as order_sizing_runtime


@pytest.fixture(autouse=True)
def reset_runtime():
    order_sizing_runtime.reset_order_sizing_runtime()
    yield
    order_sizing_runtime.reset_order_sizing_runtime()


def test_defaults_to_env(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "5")
    monkeypatch.setenv("RISK_MIN_ORDER_USD", "5")
    monkeypatch.setenv("RISK_MAX_ORDER_USD", "50")
    monkeypatch.setattr(order_sizing_runtime, "_database_url", lambda: None)

    payload = order_sizing_runtime.get_order_sizing()

    assert payload["coinbase_order_usd"] == 5.0
    assert payload["oanda_order_usd"] == 5.0
    assert payload["source"] == "env"
    assert payload["limits"]["min_usd"] == 5.0
    assert payload["limits"]["max_usd"] == 50.0


def test_coinbase_and_oanda_helpers(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "10")
    assert order_sizing_runtime.coinbase_order_usd() == 10.0
    assert order_sizing_runtime.oanda_order_usd() == 10.0


def test_set_order_sizing_clamps_to_limits(monkeypatch):
    monkeypatch.setenv("RISK_MIN_ORDER_USD", "5")
    monkeypatch.setenv("RISK_MAX_ORDER_USD", "50")
    monkeypatch.setenv("RISK_ACCOUNT_CAP_USD", "1000")
    monkeypatch.setenv("OANDA_ACCOUNT_CAP_USD", "30")
    monkeypatch.setattr(order_sizing_runtime, "_write_postgres", lambda cb, oa: None)

    result = order_sizing_runtime.set_order_sizing(coinbase_order_usd=999, oanda_order_usd=999)

    assert result["coinbase_order_usd"] == 50.0
    assert result["oanda_order_usd"] == 30.0
    assert result["source"] == "memory"


def test_set_order_sizing_clamps_below_min(monkeypatch):
    monkeypatch.setenv("RISK_MIN_ORDER_USD", "5")
    monkeypatch.setenv("RISK_MAX_ORDER_USD", "50")
    monkeypatch.setattr(order_sizing_runtime, "_write_postgres", lambda cb, oa: None)

    result = order_sizing_runtime.set_order_sizing(coinbase_order_usd=1, oanda_order_usd=2)

    assert result["coinbase_order_usd"] == 5.0
    assert result["oanda_order_usd"] == 5.0


def test_memory_override_beats_env(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "5")
    order_sizing_runtime._memory_override = order_sizing_runtime.OrderSizingState(
        coinbase_order_usd=25.0,
        oanda_order_usd=15.0,
        source="memory",
    )

    assert order_sizing_runtime.coinbase_order_usd() == 25.0
    assert order_sizing_runtime.oanda_order_usd() == 15.0
    assert order_sizing_runtime.get_order_sizing()["source"] == "memory"


def test_postgres_read_when_no_memory_override(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "5")
    pg_state = order_sizing_runtime.OrderSizingState(
        coinbase_order_usd=20.0,
        oanda_order_usd=12.0,
        source="postgres",
    )
    monkeypatch.setattr(order_sizing_runtime, "_read_postgres", lambda: (pg_state, None))

    assert order_sizing_runtime.coinbase_order_usd() == 20.0
    assert order_sizing_runtime.oanda_order_usd() == 12.0
    assert order_sizing_runtime.get_order_sizing()["source"] == "postgres"


def test_set_persists_via_write_postgres(monkeypatch):
    writes: list[tuple[float, float]] = []
    monkeypatch.setattr(
        order_sizing_runtime,
        "_write_postgres",
        lambda cb, oa: writes.append((cb, oa)) or None,
    )

    order_sizing_runtime.set_order_sizing(coinbase_order_usd=12, oanda_order_usd=8)

    assert writes == [(12.0, 8.0)]


def test_postgres_cache_reuses_read_within_ttl(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "5")
    calls = {"n": 0}

    def counting_read():
        calls["n"] += 1
        return (
            order_sizing_runtime.OrderSizingState(
                coinbase_order_usd=10.0,
                oanda_order_usd=10.0,
                source="postgres",
            ),
            None,
        )

    monkeypatch.setattr(order_sizing_runtime, "_read_postgres", counting_read)
    monkeypatch.setattr(order_sizing_runtime, "_POSTGRES_CACHE_TTL_SEC", 2.0)

    assert order_sizing_runtime.coinbase_order_usd() == 10.0
    assert order_sizing_runtime.coinbase_order_usd() == 10.0
    assert calls["n"] == 1


def test_reset_clears_runtime(monkeypatch):
    monkeypatch.setenv("RISK_DEFAULT_ORDER_USD", "5")
    monkeypatch.setattr(order_sizing_runtime, "_database_url", lambda: None)
    order_sizing_runtime._memory_override = order_sizing_runtime.OrderSizingState(
        coinbase_order_usd=99.0,
        oanda_order_usd=99.0,
        source="memory",
    )
    order_sizing_runtime.reset_order_sizing_runtime()
    assert order_sizing_runtime.get_order_sizing()["source"] == "env"
