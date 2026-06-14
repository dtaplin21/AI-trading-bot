"""Instant flatten when kill switch is enabled (Phase 4b)."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_flatten_armed = True


def reset_kill_flatten_arm() -> None:
    """Allow flatten again after kill switch is cleared — for tests and disable path."""
    global _flatten_armed
    _flatten_armed = True


def latest_close_price(symbol: str, timeframe: str = "5m") -> Optional[float]:
    """Last OHLCV close from TimescaleDB, if available."""
    try:
        from data.storage.timescale_store import TimescaleStore

        store = TimescaleStore()
        if not store.available:
            return None
        df = store.load_ohlcv(symbol.upper(), timeframe, limit=1)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])
    except Exception as exc:
        logger.debug("latest_close_price [%s]: %s", symbol, exc)
        return None


async def flatten_all_positions(*, reason: str = "KILL_SWITCH") -> dict[str, Any]:
    """Close all live (broker) and paper positions immediately."""
    global _flatten_armed
    _flatten_armed = False

    from live.live_position_monitor import get_position_monitor
    from paper_trading.paper_trader import get_paper_trader

    monitor = get_position_monitor()
    live_closed = await monitor.flatten_all(reason=reason)
    paper_closed = get_paper_trader().close_all_at_market(reason=reason.lower())

    summary = {
        "live_closed": len(live_closed),
        "paper_closed": len(paper_closed),
        "live": [
            {
                "trade_id": r.trade_id,
                "symbol": r.symbol,
                "reason": r.reason,
                "exit_price": r.exit_price,
                "outcome": r.outcome,
            }
            for r in live_closed
        ],
        "paper": paper_closed,
    }
    if live_closed or paper_closed:
        logger.warning(
            "Kill switch flatten: %d live + %d paper positions closed",
            len(live_closed),
            len(paper_closed),
        )
    return summary


async def maybe_flatten_on_kill_active() -> Optional[dict[str, Any]]:
    """
    Watcher path — flatten once per kill-switch activation (not every bar).
    Re-arms when kill switch is turned off.
    """
    global _flatten_armed

    from risk.kill_switch_runtime import is_kill_switch_active

    if not is_kill_switch_active():
        _flatten_armed = True
        return None
    if not _flatten_armed:
        return None
    _flatten_armed = False
    return await flatten_all_positions()
