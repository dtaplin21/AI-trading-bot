"""Shared WorldStateStore instance for the pipeline."""

from __future__ import annotations

from typing import Optional

from pipeline.world_state_store import WorldStateStore

_store: Optional[WorldStateStore] = None


def get_world_state_store(db_writer=None) -> WorldStateStore:
    global _store
    if _store is None:
        _store = WorldStateStore(db_writer=db_writer)
    return _store


def reset_world_state_store() -> None:
    """Clear singleton — for tests."""
    global _store
    _store = None
