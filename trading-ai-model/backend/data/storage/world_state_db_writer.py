"""Persist WorldStateStore training rows to TimescaleDB + local archive."""

from __future__ import annotations

import asyncio
import logging

from data.storage.timescale_store import TimescaleStore
from pipeline.training_archive import append_training_row

logger = logging.getLogger(__name__)


class TimescaleWorldStateWriter:
    """WorldStateDbWriter backed by TimescaleStore + append-only JSONL."""

    def __init__(self, store: TimescaleStore | None = None) -> None:
        self._store = store or TimescaleStore()

    @property
    def available(self) -> bool:
        return self._store.available

    async def save_snapshot(self, row: dict, confluence: dict | None = None) -> None:
        await asyncio.to_thread(self.save_snapshot_sync, row, confluence)

    def save_snapshot_sync(self, row: dict, confluence: dict | None = None) -> None:
        append_training_row(row)
        if self._store.available:
            self._store.insert_confluence_outcome(row, confluence=confluence)
        else:
            logger.debug("TimescaleWorldStateWriter: DB unavailable — JSONL archive only")

    def load_training_rows(self, limit: int = 50000) -> list[dict]:
        if not self._store.available:
            return []
        return self._store.load_confluence_outcomes(limit=limit)


def build_world_state_writer() -> TimescaleWorldStateWriter:
    return TimescaleWorldStateWriter()
