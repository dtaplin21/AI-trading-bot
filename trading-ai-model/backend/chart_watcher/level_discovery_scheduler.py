"""
chart_watcher/level_discovery_scheduler.py  (Phase 3)

Per-symbol scheduler for rolling level discovery, wired into the live
chart watcher. Implements the single-flight coalescing policy:

  - One discovery task can run per symbol at a time.
  - If a new trigger fires while a run is in progress, it is coalesced
    into a single pending follow-up (not skipped, not queued unboundedly).
  - Priority: regime_shift > range_escape > interval.
    Higher priority trigger wins when coalescing multiple pending requests.
  - Cooldown applies to new interval triggers only.
    Pending reruns from range_escape/regime_shift bypass cooldown.
  - Global concurrency cap across all symbols via a semaphore.

Discovery never blocks the bar pipeline — it always runs as a background
asyncio task.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

logger = logging.getLogger("level_discovery_scheduler")

LEVEL_DISCOVERY_ENABLED = os.getenv("LEVEL_DISCOVERY_ENABLED", "true").lower() == "true"
LEVEL_DISCOVERY_WINDOW_DAYS = int(os.getenv("LEVEL_DISCOVERY_WINDOW_DAYS", "60"))
LEVEL_DISCOVERY_INTERVAL_SEC = int(os.getenv("LEVEL_DISCOVERY_INTERVAL_SEC", "3600"))
LEVEL_DISCOVERY_COOLDOWN_SEC = int(os.getenv("LEVEL_DISCOVERY_COOLDOWN_SEC", "300"))
LEVEL_DISCOVERY_MAX_CONCURRENT = int(os.getenv("LEVEL_DISCOVERY_MAX_CONCURRENT", "4"))
LEVEL_DISCOVERY_RANGE_ESCAPE_PCT = float(os.getenv("LEVEL_DISCOVERY_RANGE_ESCAPE_PCT", "2.0"))
LEVEL_DISCOVERY_REGIME_GAP_PCT = float(os.getenv("LEVEL_DISCOVERY_REGIME_GAP_PCT", "8.0"))


class TriggerPriority(IntEnum):
    INTERVAL = 0
    RANGE_ESCAPE = 1
    REGIME_SHIFT = 2


@dataclass
class PendingRequest:
    priority: TriggerPriority
    reason: str
    queued_at: float = field(default_factory=time.time)


@dataclass
class EnqueueResult:
    status: str  # 'started' | 'coalesced' | 'skipped'
    reason: str = ""


class SymbolDiscoveryState:
    """Per-symbol single-flight state."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.lock = asyncio.Lock()
        self.running = False
        self.pending: Optional[PendingRequest] = None
        self.last_run_finished_at: float = 0.0
        self.last_interval_run_at: float = 0.0


class LevelDiscoveryScheduler:
    """
    Singleton scheduler shared across all symbols in the chart watcher.
    Call check_and_maybe_trigger() on every 1m bar (cheap).
    """

    def __init__(self) -> None:
        self._states: dict[str, SymbolDiscoveryState] = {}
        self._semaphore = asyncio.Semaphore(LEVEL_DISCOVERY_MAX_CONCURRENT)

    def _state(self, symbol: str) -> SymbolDiscoveryState:
        sym = symbol.upper()
        if sym not in self._states:
            self._states[sym] = SymbolDiscoveryState(sym)
        return self._states[sym]

    async def check_and_maybe_trigger(
        self,
        symbol: str,
        current_price: float,
        asset_class: str = "equity",
    ) -> EnqueueResult:
        """Cheap bar-loop entry: classify trigger and enqueue if needed."""
        if not LEVEL_DISCOVERY_ENABLED:
            return EnqueueResult("skipped", "discovery_disabled")

        priority, reason = self._classify_trigger(symbol, current_price)
        if priority is None:
            return EnqueueResult("skipped", "no_trigger")

        return await self.enqueue(symbol, priority, reason, asset_class)

    def _classify_trigger(
        self, symbol: str, current_price: float
    ) -> tuple[Optional[TriggerPriority], str]:
        """
        Synchronous classification using in-memory range cache + interval clock.
        No DB calls on the bar path.
        """
        state = self._state(symbol)
        sym = symbol.upper()
        now = time.time()

        range_cache = _RANGE_CACHE.get(sym)
        if range_cache:
            lo, hi = range_cache
            if lo is not None and hi is not None and lo > 0 and hi > 0:
                if current_price < lo or current_price > hi:
                    pct_outside = max(
                        (lo - current_price) / lo * 100 if current_price < lo else 0.0,
                        (current_price - hi) / hi * 100 if current_price > hi else 0.0,
                    )
                    if pct_outside >= LEVEL_DISCOVERY_REGIME_GAP_PCT:
                        return (
                            TriggerPriority.REGIME_SHIFT,
                            f"regime_shift_{pct_outside:.1f}pct_outside_range",
                        )
                    if pct_outside >= LEVEL_DISCOVERY_RANGE_ESCAPE_PCT:
                        return (
                            TriggerPriority.RANGE_ESCAPE,
                            f"range_escape_{pct_outside:.1f}pct_outside_range",
                        )

        if now - state.last_interval_run_at >= LEVEL_DISCOVERY_INTERVAL_SEC:
            return TriggerPriority.INTERVAL, "scheduled_interval"

        return None, ""

    async def enqueue(
        self,
        symbol: str,
        priority: TriggerPriority,
        reason: str,
        asset_class: str = "equity",
    ) -> EnqueueResult:
        state = self._state(symbol)

        if state.running:
            if state.pending is None or priority > state.pending.priority:
                state.pending = PendingRequest(priority=priority, reason=reason)
                logger.info(
                    "%s: discovery coalesced (priority=%s reason=%s)",
                    state.symbol,
                    priority.name,
                    reason,
                )
            return EnqueueResult("coalesced", reason)

        if priority == TriggerPriority.INTERVAL:
            now = time.time()
            if now - state.last_run_finished_at < LEVEL_DISCOVERY_COOLDOWN_SEC:
                return EnqueueResult("skipped", "cooldown_active")

        asyncio.create_task(
            self._run_with_coalescing(symbol, priority, reason, asset_class)
        )
        return EnqueueResult("started", reason)

    async def _run_with_coalescing(
        self,
        symbol: str,
        priority: TriggerPriority,
        reason: str,
        asset_class: str,
    ) -> None:
        state = self._state(symbol)

        async with state.lock:
            state.running = True
            try:
                async with self._semaphore:
                    await self._run_once(
                        symbol, asset_class, reason, priority, runs_coalesced=0
                    )

                coalesced_count = 0
                while state.pending is not None:
                    pending = state.pending
                    state.pending = None
                    coalesced_count += 1
                    async with self._semaphore:
                        await self._run_once(
                            symbol,
                            asset_class,
                            pending.reason,
                            pending.priority,
                            runs_coalesced=coalesced_count,
                        )
            finally:
                state.running = False
                state.last_run_finished_at = time.time()

    async def _run_once(
        self,
        symbol: str,
        asset_class: str,
        reason: str,
        priority: TriggerPriority,
        runs_coalesced: int,
    ) -> None:
        sym = symbol.upper()
        state = self._state(sym)
        logger.info(
            "%s: discovery run starting (reason=%s priority=%s coalesced=%d)",
            sym,
            reason,
            priority.name,
            runs_coalesced,
        )
        try:
            from ml.features.rolling_level_discovery import discover_symbol

            result = await asyncio.to_thread(
                discover_symbol,
                sym,
                asset_class=asset_class,
                window_days=LEVEL_DISCOVERY_WINDOW_DAYS,
                dry_run=False,
            )

            if result.error is None and result.skipped_reason is None:
                update_range_cache(sym)

            logger.info(
                "%s: discovery run finished | coverage=%.1f%% archived=%d "
                "reactivated=%d active=%d mode=%s",
                sym,
                result.coverage_pct,
                result.levels_archived,
                result.levels_reactivated,
                result.watchlist_active,
                result.merge_mode,
            )
        except Exception as exc:
            logger.error("%s: discovery run failed — %s", sym, exc, exc_info=True)
        finally:
            if priority == TriggerPriority.INTERVAL:
                state.last_interval_run_at = time.time()


_RANGE_CACHE: dict[str, tuple[float, float]] = {}


def update_range_cache(symbol: str) -> None:
    """Refresh in-memory min/max after a successful discovery run."""
    sym = symbol.upper()
    try:
        from ml.features.rolling_level_discovery import price_levels_envelope, _get_conn

        conn = _get_conn()
        try:
            envelope = price_levels_envelope(sym, conn)
        finally:
            conn.close()
        if envelope is not None:
            _RANGE_CACHE[sym] = envelope
    except Exception as exc:
        logger.debug("%s: range cache update failed: %s", sym, exc)


def warm_range_cache(symbols: list[str]) -> None:
    """Optional startup preload so first bars can detect range escape."""
    for sym in symbols:
        update_range_cache(sym)


_scheduler: Optional[LevelDiscoveryScheduler] = None


def get_discovery_scheduler() -> LevelDiscoveryScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = LevelDiscoveryScheduler()
    return _scheduler
