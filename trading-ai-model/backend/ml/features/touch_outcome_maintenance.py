"""
Automatic pending touch classification and watchlist resync.

Runs on worker startup and on a periodic interval so touch outcomes
stay current without manual backfill scripts.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from ml.features.touch_outcome_classifier import (
    LIVE_DRAIN_BATCH_SIZE,
    backfill_pending_outcomes,
    drain_stale_pending_for_symbol,
)

logger = logging.getLogger("touch_outcome_maintenance")

TOUCH_MAINTENANCE_ON_STARTUP = os.getenv(
    "TOUCH_MAINTENANCE_ON_STARTUP", "true"
).lower() in ("true", "1", "yes")
TOUCH_MAINTENANCE_INTERVAL_SEC = int(
    os.getenv("TOUCH_MAINTENANCE_INTERVAL_SEC", "300")
)
TOUCH_MAINTENANCE_STARTUP_MAX_ROWS = int(
    os.getenv("TOUCH_MAINTENANCE_STARTUP_MAX_ROWS", "50000")
)
TOUCH_MAINTENANCE_DRAIN_BATCH = int(
    os.getenv("TOUCH_MAINTENANCE_DRAIN_BATCH", str(LIVE_DRAIN_BATCH_SIZE))
)


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def _db_available() -> bool:
    url = _database_url()
    return bool(url) and not is_database_url_placeholder(url)


def maintain_symbol(
    symbol: str,
    *,
    max_backfill_rows: int | None = None,
    drain_batch: int | None = None,
) -> dict[str, Any]:
    """
    Classify stale pending touches, reaggregate stats, resync watchlist.
    Safe to call repeatedly — reaggregate rebuilds from classified rows only.
    """
    from ml.features.level_intelligence import get_system

    sym = symbol.upper()
    if not _db_available():
        return {"symbol": sym, "skipped": "no_db"}

    spec_asset = "equity"
    from config.symbols import get_symbol_or_none

    spec = get_symbol_or_none(sym)
    if spec:
        spec_asset = spec.asset_class

    system = get_system(sym, spec_asset)
    drained = drain_stale_pending_for_symbol(
        system,
        batch_size=drain_batch or TOUCH_MAINTENANCE_DRAIN_BATCH,
    )

    backfill_stats = backfill_pending_outcomes(
        sym,
        max_rows=max_backfill_rows,
    )

    conn = connect_psycopg2(_database_url())
    try:
        from ml.features.rolling_level_discovery import sync_watchlist_for_symbol

        active = sync_watchlist_for_symbol(conn, sym)
    finally:
        conn.close()

    result = {
        "symbol": sym,
        "drained_live": drained,
        "backfill": backfill_stats,
        "watchlist_active": active,
    }
    logger.info(
        "%s touch maintenance: drained=%d classified=%d watchlist_active=%d",
        sym,
        drained,
        backfill_stats.get("classified", 0),
        active,
    )
    return result


def maintain_symbols(
    symbols: list[str],
    *,
    max_backfill_rows: int | None = None,
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        try:
            summary[sym.upper()] = maintain_symbol(
                sym,
                max_backfill_rows=max_backfill_rows,
            )
        except Exception as exc:
            logger.error("%s touch maintenance failed: %s", sym, exc)
            summary[sym.upper()] = {"error": str(exc)}
    return summary


async def run_startup_maintenance(symbols: list[str]) -> None:
    """Background-friendly startup drain for all watched symbols."""
    if not TOUCH_MAINTENANCE_ON_STARTUP or not symbols:
        return
    import asyncio

    logger.info(
        "Touch maintenance startup for %d symbols (max_rows=%d)",
        len(symbols),
        TOUCH_MAINTENANCE_STARTUP_MAX_ROWS,
    )
    await asyncio.to_thread(
        maintain_symbols,
        symbols,
        max_backfill_rows=TOUCH_MAINTENANCE_STARTUP_MAX_ROWS,
    )


async def run_periodic_maintenance_loop(symbols: list[str]) -> None:
    """Periodic incremental pending classification."""
    import asyncio

    interval = max(60, TOUCH_MAINTENANCE_INTERVAL_SEC)
    while True:
        await asyncio.sleep(interval)
        if not _db_available():
            continue
        logger.debug("Touch maintenance periodic tick (%d symbols)", len(symbols))
        await asyncio.to_thread(maintain_symbols, symbols)
