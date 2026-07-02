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


def is_valid_bar_close(price: float | None) -> bool:
    """Reject zero/negative bar closes (bad forex tick data) for discovery triggers."""
    return price is not None and price > 0


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


def normalize_trigger_reason(reason: str, priority: TriggerPriority) -> str:
    """Map scheduler reason/priority to level_discovery_runs.trigger_reason."""
    if reason.startswith("regime_shift"):
        return "regime_shift"
    if reason.startswith("range_escape"):
        return "range_escape"
    if reason == "scheduled_interval" or priority == TriggerPriority.INTERVAL:
        return "interval"
    if reason == "startup":
        return "startup"
    if reason == "manual":
        return "manual"
    if priority == TriggerPriority.REGIME_SHIFT:
        return "regime_shift"
    if priority == TriggerPriority.RANGE_ESCAPE:
        return "range_escape"
    return reason


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

        if not is_valid_bar_close(current_price):
            return EnqueueResult("skipped", "invalid_bar_close")

        priority, reason = self._classify_trigger(symbol, current_price)
        if priority is None:
            return EnqueueResult("skipped", "no_trigger")

        return await self.enqueue(symbol, priority, reason, asset_class)

    async def maybe_enqueue_startup_discovery(self, symbols: list[str]) -> None:
        """After cache warm, enqueue one run per symbol with a stale envelope vs last close."""
        if not LEVEL_DISCOVERY_ENABLED:
            return

        def collect_candidates() -> list[tuple[str, TriggerPriority, float]]:
            hits: list[tuple[str, TriggerPriority, float]] = []
            for sym in symbols:
                sym_u = sym.upper()
                if sym_u not in _RANGE_CACHE:
                    continue
                last_close = fetch_last_close(sym_u)
                if last_close is None:
                    continue
                priority, _ = self._classify_escape_trigger(sym_u, last_close)
                if priority is not None:
                    hits.append((sym_u, priority, last_close))
            return hits

        candidates = await asyncio.to_thread(collect_candidates)
        if not candidates:
            return

        from config.symbols import get_symbol_or_none

        for sym_u, priority, last_close in candidates:
            spec = get_symbol_or_none(sym_u)
            asset_class = spec.asset_class if spec else "equity"
            result = await self.enqueue(sym_u, priority, "startup", asset_class)
            logger.info(
                "%s: startup discovery %s priority=%s close=%.4f envelope=%s",
                sym_u,
                result.status,
                priority.name,
                last_close,
                _RANGE_CACHE.get(sym_u),
            )

    def _classify_escape_trigger(
        self, symbol: str, current_price: float
    ) -> tuple[Optional[TriggerPriority], str]:
        """Range escape / regime shift only — no interval fallback."""
        if not is_valid_bar_close(current_price):
            return None, ""

        sym = symbol.upper()
        range_cache = _RANGE_CACHE.get(sym)
        if not range_cache:
            return None, ""

        lo, hi = range_cache
        if lo is None or hi is None or lo <= 0 or hi <= 0:
            return None, ""

        if current_price >= lo and current_price <= hi:
            return None, ""

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
        return None, ""

    def _classify_trigger(
        self, symbol: str, current_price: float
    ) -> tuple[Optional[TriggerPriority], str]:
        """
        Synchronous classification using in-memory range cache + interval clock.
        No DB calls on the bar path.
        """
        priority, reason = self._classify_escape_trigger(symbol, current_price)
        if priority is not None:
            return priority, reason

        state = self._state(symbol)
        now = time.time()
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

            trigger_reason = normalize_trigger_reason(reason, priority)
            result = await asyncio.to_thread(
                discover_symbol,
                sym,
                asset_class=asset_class,
                window_days=LEVEL_DISCOVERY_WINDOW_DAYS,
                dry_run=False,
                trigger_reason=trigger_reason,
                runs_coalesced=runs_coalesced,
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


def fetch_last_close(symbol: str) -> float | None:
    """Latest OHLCV close for startup stale-book checks."""
    sym = symbol.upper()
    try:
        from ml.features.rolling_level_discovery import _get_conn

        conn = _get_conn()
        try:
            cur = conn.cursor()
            for timeframe in ("1m", "5m"):
                cur.execute(
                    """
                    SELECT close
                    FROM ohlcv_candles
                    WHERE symbol = %s AND timeframe = %s AND close > 0
                    ORDER BY time DESC
                    LIMIT 1
                    """,
                    (sym, timeframe),
                )
                row = cur.fetchone()
                if row:
                    return float(row[0])
            return None
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("%s: fetch_last_close failed — %s", sym, exc)
        return None


def _fetch_and_store_range_cache(symbol: str) -> tuple[float, float] | None:
    sym = symbol.upper()
    from ml.features.rolling_level_discovery import price_levels_envelope, _get_conn

    conn = _get_conn()
    try:
        envelope = price_levels_envelope(sym, conn)
    finally:
        conn.close()
    if envelope is not None:
        _RANGE_CACHE[sym] = envelope
    return envelope


def update_range_cache(symbol: str) -> None:
    """Refresh in-memory min/max after a successful discovery run."""
    sym = symbol.upper()
    try:
        _fetch_and_store_range_cache(sym)
    except Exception as exc:
        logger.debug("%s: range cache update failed: %s", sym, exc)


def warm_range_cache(symbols: list[str]) -> None:
    """Preload envelope min/max at startup so first bars can detect range escape."""
    for sym in symbols:
        sym_u = sym.upper()
        try:
            envelope = _fetch_and_store_range_cache(sym_u)
            if envelope is not None:
                logger.info(
                    "%s: range cache warmed envelope=[%.4f, %.4f]",
                    sym_u,
                    envelope[0],
                    envelope[1],
                )
            else:
                logger.info("%s: range cache warm — no active levels", sym_u)
        except Exception as exc:
            logger.warning("%s: range cache warm failed — %s", sym_u, exc)


_scheduler: Optional[LevelDiscoveryScheduler] = None


def get_discovery_scheduler() -> LevelDiscoveryScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = LevelDiscoveryScheduler()
    return _scheduler
