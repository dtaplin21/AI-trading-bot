"""
data/storage/feature_store.py

In-memory feature cache with TTL expiry.

Was: get() → None, set() → pass
Now: real cache that stores computed features between pipeline stages

Connects to:
  - TradingPipelineSupervisor — caches features so 13 agents don't
    each recompute RSI/MACD/ATR independently for the same candle
  - FeaturePipeline — writes computed features here after computation
  - Method agents — read features from here instead of recomputing

Why this matters:
  Without this, every method agent recomputes all indicators from scratch.
  With it, features are computed once per bar and shared across all agents.
  For 23 symbols × 13 agents this is a significant performance improvement.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FeatureStore:
    """
    In-memory key-value store for computed features.
    Keys expire after TTL seconds (default 60 — one bar lifetime).

    Thread-safe for concurrent agent access.
    """

    def __init__(self, default_ttl: int = 60):
        self.default_ttl = default_ttl
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)
        self._lock = threading.RLock()

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> None:
        """
        Store a value with TTL expiry.

        Args:
            key:   cache key, e.g. "EURUSD:5m:features:1704067200"
            value: any serializable value (dict, float, list, etc.)
            ttl:   seconds until expiry (default: self.default_ttl)
        """
        expires_at = time.monotonic() + (ttl or self.default_ttl)
        with self._lock:
            self._store[key] = (value, expires_at)

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a value. Returns None if missing or expired.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def purge_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
        return len(expired)

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    # ── Convenience helpers for the trading pipeline ──────────────────────────

    @staticmethod
    def feature_key(symbol: str, timeframe: str, timestamp) -> str:
        """Standard key format for feature caching."""
        ts = int(timestamp.timestamp()) if hasattr(timestamp, "timestamp") else int(timestamp)
        return f"{symbol.upper()}:{timeframe}:features:{ts}"

    @staticmethod
    def signal_key(symbol: str, timeframe: str, timestamp) -> str:
        ts = int(timestamp.timestamp()) if hasattr(timestamp, "timestamp") else int(timestamp)
        return f"{symbol.upper()}:{timeframe}:signal:{ts}"

    @staticmethod
    def model_output_key(symbol: str, method: str, timestamp) -> str:
        ts = int(timestamp.timestamp()) if hasattr(timestamp, "timestamp") else int(timestamp)
        return f"{symbol.upper()}:{method}:output:{ts}"

    def set_features(self, symbol: str, timeframe: str, timestamp, features: dict) -> None:
        key = self.feature_key(symbol, timeframe, timestamp)
        self.set(key, features, ttl=120)  # 2 bar TTL for features

    def get_features(self, symbol: str, timeframe: str, timestamp) -> Optional[dict]:
        key = self.feature_key(symbol, timeframe, timestamp)
        value = self.get(key)
        return value if isinstance(value, dict) else None

    def set_signal(self, symbol: str, timeframe: str, timestamp, signal: dict) -> None:
        key = self.signal_key(symbol, timeframe, timestamp)
        self.set(key, signal, ttl=300)  # 5 minute TTL for signals

    def get_signal(self, symbol: str, timeframe: str, timestamp) -> Optional[dict]:
        key = self.signal_key(symbol, timeframe, timestamp)
        value = self.get(key)
        return value if isinstance(value, dict) else None


# Module-level singleton — shared across all pipeline components
_store = FeatureStore(default_ttl=60)


def get_feature_store() -> FeatureStore:
    """Return the shared FeatureStore singleton."""
    return _store


def get(key: str) -> Optional[Any]:
    return _store.get(key)


def set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    _store.set(key, value, ttl)


def delete(key: str) -> None:
    _store.delete(key)


def get_features(symbol: str, timeframe: str, timestamp) -> Optional[dict]:
    return _store.get_features(symbol, timeframe, timestamp)


def set_features(symbol: str, timeframe: str, timestamp, features: dict) -> None:
    _store.set_features(symbol, timeframe, timestamp, features)
