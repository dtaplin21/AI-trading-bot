"""
trading_mcp/tools/risk.py

MCP tool implementations for risk and ops control.
"""
from __future__ import annotations

import json
import logging
import os

from risk.kill_switch_runtime import (
    get_kill_switch_status,
    is_kill_switch_active,
    set_kill_switch_enabled,
)

logger = logging.getLogger(__name__)


async def get_risk_summary() -> str:
    """Return current risk state: kill switch, daily loss, open positions, caps."""
    kill_status = get_kill_switch_status()
    paper = os.getenv("PAPER_MODE", "true")
    max_pos = os.getenv("RISK_MAX_CONCURRENT", "3")
    max_loss = os.getenv("RISK_MAX_DAILY_LOSS_PCT", "2.0")
    max_usd = os.getenv("RISK_MAX_POSITION_USD", "500")

    open_positions = []
    try:
        from live.live_position_monitor import get_position_monitor

        for p in get_position_monitor().open_positions():
            open_positions.append(
                {
                    "trade_id": p.trade_id,
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "tp": p.target_price,
                    "sl": p.stop_price,
                    "bars_held": p.bars_held,
                    "ev_pct": p.ev_pct,
                }
            )
    except Exception:
        pass

    return json.dumps(
        {
            "kill_switch": is_kill_switch_active(),
            "kill_switch_status": kill_status,
            "paper_mode": paper,
            "max_concurrent": max_pos,
            "max_daily_loss_pct": max_loss,
            "max_position_usd": max_usd,
            "open_positions": open_positions,
            "open_count": len(open_positions),
        },
        indent=2,
        default=str,
    )


async def set_kill_switch(enabled: bool) -> str:
    """Toggle kill switch — same path as PUT /risk/kill-switch."""
    status = await set_kill_switch_enabled(enabled)
    return json.dumps({"ok": True, **status}, indent=2, default=str)
