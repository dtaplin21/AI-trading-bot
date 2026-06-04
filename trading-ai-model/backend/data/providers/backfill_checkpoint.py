"""Resumable progress tracking for Polygon OHLCV backfill."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_PENDING_ENTRY: dict[str, Any] = {
    "status": "pending",
    "last_date": None,
    "last_contract": None,
    "bars_saved": 0,
    "chunks_done": 0,
    "last_updated": None,
}


class CheckpointManager:
    """
    Saves progress after each successful symbol+chunk.

    File shape:
      { "timeframe", "start", "end", "symbols": { SYM: { status, last_date, ... } } }
    """

    def __init__(
        self,
        path: Path,
        *,
        timeframe: str,
        start: str,
        end: str,
        symbols: list[str],
    ) -> None:
        self.path = path
        self.timeframe = timeframe
        self.start = start
        self.end = end
        self.symbols = symbols
        self._data: dict[str, Any] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        if not self.path.exists():
            self._init_fresh()
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Checkpoint read failed (%s) — starting fresh", exc)
            self._init_fresh()
            return
        if (
            self._data.get("timeframe") != self.timeframe
            or self._data.get("start") != self.start
            or self._data.get("end") != self.end
        ):
            logger.warning(
                "Checkpoint is for a different job (was %s→%s %s) — starting fresh. Use --reset to clear.",
                self._data.get("start"),
                self._data.get("end"),
                self._data.get("timeframe"),
            )
            self._init_fresh()
            return
        done = self._count_status("done")
        remaining = len(self.symbols) - done
        logger.info(
            "Checkpoint loaded: %d symbols done, %d remaining",
            done,
            remaining,
        )

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()
            logger.info("Checkpoint deleted — starting from scratch")
        self._init_fresh()

    def reset_symbols(self, symbols: Iterable[str]) -> list[str]:
        """Set symbols back to pending (keeps forex/crypto/equity progress intact)."""
        if not self._data.get("symbols"):
            return []
        reset: list[str] = []
        for sym in symbols:
            key = sym.upper()
            if key not in self._data["symbols"]:
                continue
            self._data["symbols"][key] = dict(_PENDING_ENTRY)
            reset.append(key)
        if reset:
            self._save()
            logger.info("Checkpoint reset to pending: %s", ", ".join(reset))
        return reset

    def reset_futures_for_rebackfill(
        self,
        futures_symbols: Iterable[str],
        *,
        only_zero_bars: bool = False,
    ) -> list[str]:
        """
        Re-queue futures after a bad run (e.g. C:MES with 0 bars).

        only_zero_bars=True — only reset futures marked done with bars_saved==0.
        only_zero_bars=False — reset all listed futures regardless of progress.
        """
        to_reset: list[str] = []
        for sym in futures_symbols:
            key = sym.upper()
            entry = self._data.get("symbols", {}).get(key)
            if not entry:
                continue
            if only_zero_bars:
                if entry.get("status") != "done" or entry.get("bars_saved", 0) > 0:
                    continue
            to_reset.append(key)
        return self.reset_symbols(to_reset)

    def _init_fresh(self) -> None:
        self._data = {
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "timeframe": self.timeframe,
            "start": self.start,
            "end": self.end,
            "symbols": {sym: dict(_PENDING_ENTRY) for sym in self.symbols},
        }
        self._save()

    def get_resume_date(self, symbol: str) -> str | None:
        resume, _ = self.get_resume_context(symbol)
        return resume

    def get_resume_context(self, symbol: str) -> tuple[str | None, str | None]:
        """Return (resume_date YYYY-MM-DD or None if done, last_contract code or None)."""
        entry = self._data.get("symbols", {}).get(symbol, {})
        if entry.get("status") == "done":
            return None, None
        last_date = entry.get("last_date")
        last_contract = entry.get("last_contract")
        if last_date:
            resume = datetime.fromisoformat(last_date) + timedelta(days=1)
            return resume.strftime("%Y-%m-%d"), last_contract
        return self.start, last_contract

    def mark_chunk_done(
        self,
        symbol: str,
        chunk_end: str,
        bars_added: int,
        *,
        last_contract: str | None = None,
    ) -> None:
        entry = self._data["symbols"].setdefault(symbol, {})
        entry["status"] = "in_progress"
        entry["last_date"] = chunk_end
        if last_contract:
            entry["last_contract"] = last_contract
        entry["bars_saved"] = entry.get("bars_saved", 0) + bars_added
        entry["chunks_done"] = entry.get("chunks_done", 0) + 1
        entry["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
        self._save()

    def mark_symbol_done(self, symbol: str) -> None:
        entry = self._data["symbols"].setdefault(symbol, {})
        entry["status"] = "done"
        entry["last_date"] = self.end
        entry["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
        self._save()
        logger.info("%s complete — %d bars saved", symbol, entry.get("bars_saved", 0))

    def is_done(self, symbol: str) -> bool:
        return self._data.get("symbols", {}).get(symbol, {}).get("status") == "done"

    def print_status(self) -> None:
        print(f"\n{'=' * 65}")
        print(f"  Backfill progress: {self.start} → {self.end} ({self.timeframe})")
        print(f"  Checkpoint: {self.path}")
        print(f"{'=' * 65}")
        print(
            f"  {'Symbol':<8} {'Status':<12} {'Contract':<10} {'Last date':<12} "
            f"{'Bars':>8} {'Chunks':>6}"
        )
        print(f"  {'-' * 62}")
        for sym, entry in self._data.get("symbols", {}).items():
            status = entry.get("status", "pending")
            last_date = entry.get("last_date") or "—"
            contract = entry.get("last_contract") or "—"
            bars = entry.get("bars_saved", 0)
            chunks = entry.get("chunks_done", 0)
            icon = "✓" if status == "done" else "→" if status == "in_progress" else "·"
            print(
                f"  {icon} {sym:<8} {status:<12} {contract:<10} {last_date:<12} "
                f"{bars:>8,} {chunks:>6}"
            )
        done = self._count_status("done")
        total = len(self._data.get("symbols", {}))
        print(f"{'=' * 65}")
        print(f"  Done: {done}/{total} symbols | {total - done} remaining")
        print(f"{'=' * 65}\n")

    def _count_status(self, status: str) -> int:
        return sum(
            1 for e in self._data.get("symbols", {}).values() if e.get("status") == status
        )

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Checkpoint save failed: %s", exc)
