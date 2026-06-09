"""
Feature cache layer — in-memory TTL cache for pipeline computed features.

TradingPipelineSupervisor caches fused feature dicts between bars so downstream
agents and audit can reuse without recomputing method outputs.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL = int(os.getenv("FEATURE_CACHE_TTL_SECONDS", "300"))
_MAX_ENTRIES = int(os.getenv("FEATURE_CACHE_MAX_ENTRIES", "2000"))


class FeatureStore:
    """Thread-safe in-memory feature cache with TTL eviction."""

    def __init__(
        self,
        ttl_seconds: int = _DEFAULT_TTL,
        max_entries: int = _MAX_ENTRIES,
    ) -> None:
        self._ttl = max(30, ttl_seconds)
        self._max_entries = max(100, max_entries)
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        expires_at = time.monotonic() + ttl
        with self._lock:
            if len(self._data) >= self._max_entries:
                self._evict_oldest()
            self._data[key] = (expires_at, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            now = time.monotonic()
            active = sum(1 for exp, _ in self._data.values() if exp > now)
            return {"entries": len(self._data), "active": active, "max": self._max_entries}

    def _evict_oldest(self) -> None:
        if not self._data:
            return
        oldest_key = min(self._data, key=lambda k: self._data[k][0])
        del self._data[oldest_key]


_store: FeatureStore | None = None


def get_feature_store() -> FeatureStore:
    global _store
    if _store is None:
        _store = FeatureStore()
    return _store
