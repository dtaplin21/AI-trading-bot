"""
live/live_position_monitor.py

Watches all open live positions and closes them when:
  1. Price hits the target (TP)
  2. Price hits the stop (SL)
  3. A kill switch is thrown
  4. Max hold time expires (optional safety net)

This is essential for brokers that don't support native bracket orders
(Coinbase, tastytrade futures). OANDA handles TP/SL natively but we
still track here for the learning loop.

Called on every new bar by TradingPipelineSupervisor (live mode).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from live.broker_router import get_broker_router

logger = logging.getLogger("LivePositionMonitor")

MAX_BARS_HELD = int(os.getenv("MAX_BARS_HELD", "100"))


@dataclass
class LivePosition:
    """One open live position being monitored."""
    trade_id: str
    symbol: str
    side: str  # LONG | SHORT
    entry_price: float
    target_price: float
    stop_price: float
    quantity: float
    broker_order_id: str
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bars_held: int = 0
    tp_pct: float = 0.0
    sl_pct: float = 0.0
    ev_pct: float = 0.0
    touch_count: int = 0
    hold_rate: float = 0.0


@dataclass
class CloseResult:
    trade_id: str
    symbol: str
    reason: str  # TP | SL | KILL_SWITCH | MAX_BARS | MANUAL
    exit_price: float
    pnl_pct: float
    bars_held: int
    outcome: str  # WIN | LOSS | BREAKEVEN


class LivePositionMonitor:
    """Singleton that tracks all open live positions across all symbols."""

    def __init__(self) -> None:
        self._positions: Dict[str, LivePosition] = {}
        self._router = get_broker_router()
        self.paper_mode = False
        logger.info("LivePositionMonitor started")

    def configure(self, *, paper_mode: bool) -> None:
        self.paper_mode = paper_mode

    # ── Register / remove ─────────────────────────────────────────────────────

    def register(self, position: LivePosition) -> None:
        """Called by LiveExecutionAgent after a fill is confirmed."""
        self._positions[position.trade_id] = position
        logger.info(
            "Position registered | %s %s entry=%.5f tp=%.5f sl=%.5f qty=%.4f",
            position.symbol,
            position.side,
            position.entry_price,
            position.target_price,
            position.stop_price,
            position.quantity,
        )

    def remove(self, trade_id: str) -> None:
        self._positions.pop(trade_id, None)

    def open_positions(self) -> List[LivePosition]:
        return list(self._positions.values())

    # ── Main bar check ────────────────────────────────────────────────────────

    async def on_bar(self, symbol: str, high: float, low: float, close: float) -> List[CloseResult]:
        """
        Called on every completed bar for this symbol.
        Checks all open positions for this symbol against TP/SL.
        Returns list of CloseResults for any positions that were closed.
        """
        closed: List[CloseResult] = []
        kill_switch = os.getenv("RISK_KILL_SWITCH", "false").lower() == "true"

        for trade_id, pos in list(self._positions.items()):
            if pos.symbol != symbol:
                continue

            pos.bars_held += 1
            reason = None

            if kill_switch:
                reason = "KILL_SWITCH"
            elif pos.side == "LONG":
                if high >= pos.target_price:
                    reason = "TP"
                elif low <= pos.stop_price:
                    reason = "SL"
            else:
                if low <= pos.target_price:
                    reason = "TP"
                elif high >= pos.stop_price:
                    reason = "SL"

            if reason is None and pos.bars_held >= MAX_BARS_HELD:
                reason = "MAX_BARS"
                logger.warning(
                    "%s position %s hit MAX_BARS_HELD=%d — force closing",
                    symbol,
                    trade_id,
                    MAX_BARS_HELD,
                )

            if reason:
                exit_price = self._estimate_exit_price(pos, reason, high, low, close)
                result = await self._close(pos, reason, exit_price)
                closed.append(result)

        return closed

    # ── Close logic ──────────────────────────────────────────────────────────

    async def _close(
        self,
        pos: LivePosition,
        reason: str,
        exit_price: float,
    ) -> CloseResult:
        """Submit market close order and record the result."""
        logger.info(
            "Closing %s %s | reason=%s exit=%.5f entry=%.5f bars=%d",
            pos.symbol,
            pos.side,
            reason,
            exit_price,
            pos.entry_price,
            pos.bars_held,
        )

        try:
            if not self.paper_mode:
                broker = self._router.get(pos.symbol)
                await broker.close_position(pos.symbol, pos.quantity)
        except Exception as exc:
            logger.error(
                "Close order failed for %s: %s — still recording as closed",
                pos.symbol,
                exc,
            )

        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        outcome = "WIN" if pnl_pct > 0.01 else ("LOSS" if pnl_pct < -0.01 else "BREAKEVEN")

        result = CloseResult(
            trade_id=pos.trade_id,
            symbol=pos.symbol,
            reason=reason,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            bars_held=pos.bars_held,
            outcome=outcome,
        )

        await self._record_close(pos, result)
        self.remove(pos.trade_id)

        logger.info(
            "Position closed | %s | outcome=%s pnl=%.3f%% bars=%d",
            pos.symbol,
            outcome,
            pnl_pct,
            pos.bars_held,
        )
        return result

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _estimate_exit_price(
        self,
        pos: LivePosition,
        reason: str,
        high: float,
        low: float,
        close: float,
    ) -> float:
        """Use TP/SL price when hit, close price otherwise."""
        if reason == "TP":
            return pos.target_price
        if reason == "SL":
            return pos.stop_price
        return close

    async def _record_close(self, pos: LivePosition, result: CloseResult) -> None:
        """Update live_trades table with exit data."""
        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn or is_database_url_placeholder(dsn):
            return
        try:
            conn = connect_psycopg2(dsn)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE live_trades SET
                            exit_price  = %s,
                            exit_time   = NOW(),
                            exit_reason = %s,
                            bars_held   = %s,
                            pnl_pct     = %s,
                            outcome     = %s,
                            status      = 'CLOSED'
                        WHERE trade_id = %s
                        """,
                        (
                            result.exit_price,
                            result.reason,
                            result.bars_held,
                            result.pnl_pct,
                            result.outcome,
                            result.trade_id,
                        ),
                    )
            conn.close()
        except Exception as exc:
            logger.error("_record_close DB write failed: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

_monitor: LivePositionMonitor | None = None


def get_position_monitor() -> LivePositionMonitor:
    global _monitor
    if _monitor is None:
        _monitor = LivePositionMonitor()
    return _monitor


def reset_position_monitor() -> None:
    """Reset singleton — for tests."""
    global _monitor
    _monitor = None
