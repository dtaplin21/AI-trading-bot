"""Tests for FeatureStore TTL cache."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data.storage import feature_store as feature_store_module
from data.storage.feature_store import FeatureStore, get_feature_store


@pytest.fixture
def store():
    return FeatureStore(default_ttl=1)


def test_set_and_get_round_trip(store):
    store.set("key", {"rsi": 55.0})
    assert store.get("key") == {"rsi": 55.0}


def test_expired_entry_returns_none(store):
    with patch("data.storage.feature_store.time.monotonic", side_effect=[100.0, 102.0, 102.0]):
        store.set("key", 42, ttl=1)
        assert store.get("key") is None
        assert not store.exists("key")


def test_delete_clear_and_purge(store):
    with patch("data.storage.feature_store.time.monotonic", return_value=100.0):
        store.set("a", 1, ttl=60)
        store.set("b", 2, ttl=1)
    with patch("data.storage.feature_store.time.monotonic", return_value=102.0):
        assert store.purge_expired() == 1
    assert store.size() == 1
    store.delete("a")
    assert store.size() == 0
    store.set("c", 3)
    store.clear()
    assert store.size() == 0


def test_feature_and_signal_helpers(store):
    ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    store.set_features("eurusd", "5m", ts, {"rsi_14": 61.0})
    cached = store.get_features("EURUSD", "5m", ts)
    assert cached == {"rsi_14": 61.0}

    store.set_signal("EURUSD", "5m", ts, {"direction": "long"})
    assert store.get_signal("EURUSD", "5m", ts) == {"direction": "long"}


def test_module_helpers_use_singleton():
    ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
    feature_store_module.set_features("TSLA", "5m", ts, {"atr_14": 1.2})
    assert feature_store_module.get_features("TSLA", "5m", ts) == {"atr_14": 1.2}
    feature_store_module.delete(FeatureStore.feature_key("TSLA", "5m", ts))
    assert feature_store_module.get_features("TSLA", "5m", ts) is None


def test_get_feature_store_returns_singleton():
    assert get_feature_store() is feature_store_module._store
