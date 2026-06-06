"""Align backfill checkpoint status with OHLCV on disk (CSV) and in TimescaleDB."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from data.providers.polygon_backfill import parse_date

if TYPE_CHECKING:
    from data.providers.backfill_checkpoint import CheckpointManager
    from data.storage.timescale_store import TimescaleStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageStats:
    rows: int
    first_date: str  # YYYY-MM-DD
    last_date: str  # YYYY-MM-DD
    source: str  # csv | db | csv+db


def _ts_to_date(ts: str) -> str:
    raw = (ts or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).strftime("%Y-%m-%d")
    return parse_date(raw[:10]).strftime("%Y-%m-%d")


def inspect_ohlcv_csv(path: Path) -> StorageStats | None:
    """Read first/last row timestamps and row count without loading the full file."""
    if not path.is_file() or path.stat().st_size == 0:
        return None

    rows = 0
    first_ts = ""
    last_ts = ""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ts = row.get("timestamp") or row.get("time") or row.get("date") or ""
            if not ts:
                continue
            rows += 1
            if rows == 1:
                first_ts = ts
            last_ts = ts

    if rows == 0:
        return None

    first_date = _ts_to_date(first_ts)
    last_date = _ts_to_date(last_ts)
    if not first_date or not last_date:
        return None
    return StorageStats(rows=rows, first_date=first_date, last_date=last_date, source="csv")


def inspect_ohlcv_db(store: TimescaleStore, symbol: str, timeframe: str) -> StorageStats | None:
    count, min_t, max_t = store.ohlcv_storage_stats(symbol, timeframe)
    if count <= 0 or min_t is None or max_t is None:
        return None
    return StorageStats(
        rows=count,
        first_date=min_t.strftime("%Y-%m-%d"),
        last_date=max_t.strftime("%Y-%m-%d"),
        source="db",
    )


def csv_path(data_dir: Path, symbol: str, timeframe: str) -> Path:
    return data_dir / f"{symbol.upper()}_{timeframe}.csv"


def merge_storage_stats(
    csv_stats: StorageStats | None,
    db_stats: StorageStats | None,
) -> StorageStats | None:
    if csv_stats is None:
        return db_stats
    if db_stats is None:
        return csv_stats
    return StorageStats(
        rows=max(csv_stats.rows, db_stats.rows),
        first_date=min(csv_stats.first_date, db_stats.first_date),
        last_date=max(csv_stats.last_date, db_stats.last_date),
        source="csv+db",
    )


def _covers_job(stats: StorageStats, job_start: str, job_end: str) -> bool:
    return stats.first_date <= job_start and stats.last_date >= job_end


def _apply_stats_to_entry(
    entry: dict,
    stats: StorageStats | None,
    *,
    job_start: str,
    job_end: str,
    now: str,
) -> bool:
    """Update one checkpoint symbol entry. Returns True if entry changed."""
    if stats is None:
        if entry.get("status") != "pending" or entry.get("bars_saved", 0):
            entry.update(
                {
                    "status": "pending",
                    "last_date": None,
                    "last_contract": None,
                    "bars_saved": 0,
                    "chunks_done": 0,
                    "last_updated": now,
                }
            )
            return True
        return False

    if _covers_job(stats, job_start, job_end):
        entry.update(
            {
                "status": "done",
                "last_date": job_end,
                "bars_saved": stats.rows,
                "last_updated": now,
            }
        )
        return True

    if stats.first_date > job_start:
        entry.update(
            {
                "status": "in_progress",
                "last_date": None,
                "bars_saved": stats.rows,
                "last_updated": now,
            }
        )
        return True

    entry.update(
        {
            "status": "in_progress",
            "last_date": stats.last_date,
            "bars_saved": stats.rows,
            "last_updated": now,
        }
    )
    return True


def sync_checkpoint_from_storage(
    checkpoint: CheckpointManager,
    data_dir: Path,
    *,
    timeframe: str,
    symbols: list[str],
    store: Optional[TimescaleStore] = None,
    use_csv: bool = True,
    use_db: bool = True,
) -> list[str]:
    """
    Update checkpoint symbol entries from CSV files and/or TimescaleDB.

    - done: storage spans job start → end
    - in_progress: partial coverage (resume from job start or last_date)
    - pending: no data in either source

    Returns list of symbols updated.
    """
    from data.storage.timescale_store import TimescaleStore

    if store is None and use_db:
        store = TimescaleStore()

    job_start = checkpoint.start
    job_end = checkpoint.end
    now = datetime.now(tz=timezone.utc).isoformat()
    updated: list[str] = []

    for sym in symbols:
        key = sym.upper()
        csv_stats = inspect_ohlcv_csv(csv_path(data_dir, key, timeframe)) if use_csv else None
        db_stats = (
            inspect_ohlcv_db(store, key, timeframe)
            if use_db and store is not None and store.available
            else None
        )
        stats = merge_storage_stats(csv_stats, db_stats)
        entry = checkpoint._data.setdefault("symbols", {}).setdefault(key, {})

        before = (
            entry.get("status"),
            entry.get("last_date"),
            entry.get("bars_saved"),
        )
        if _apply_stats_to_entry(entry, stats, job_start=job_start, job_end=job_end, now=now):
            after = (
                entry.get("status"),
                entry.get("last_date"),
                entry.get("bars_saved"),
            )
            if before != after:
                updated.append(key)
                if stats:
                    logger.info(
                        "%s: storage sync → %s (%d bars, %s → %s via %s)",
                        key,
                        entry["status"],
                        stats.rows,
                        stats.first_date,
                        stats.last_date,
                        stats.source,
                    )
                else:
                    logger.info("%s: storage sync → pending (no CSV/DB data)", key)

    if updated:
        checkpoint._save()
        logger.info("Storage sync updated %d symbol(s)", len(updated))
    else:
        logger.info("Storage sync: checkpoint already matches CSV/DB")

    return updated


# Backward-compatible alias
def sync_checkpoint_from_csv(
    checkpoint: CheckpointManager,
    data_dir: Path,
    *,
    timeframe: str,
    symbols: list[str],
) -> list[str]:
    return sync_checkpoint_from_storage(
        checkpoint,
        data_dir,
        timeframe=timeframe,
        symbols=symbols,
        use_csv=True,
        use_db=True,
    )
