"""
trading_mcp/tools/risk.py

MCP tool implementations for risk and ops control.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


async def get_risk_summary() -> str:
    """Return current risk state: kill switch, daily loss, open positions, caps."""
    kill = os.getenv("RISK_KILL_SWITCH", "false")
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
            "kill_switch": kill,
            "paper_mode": paper,
            "max_concurrent": max_pos,
            "max_daily_loss_pct": max_loss,
            "max_position_usd": max_usd,
            "open_positions": open_positions,
            "open_count": len(open_positions),
        },
        indent=2,
    )


async def set_kill_switch(enabled: bool) -> str:
    """
    Toggle the kill switch.
    Sets the env var for this process — worker must be restarted to propagate.
    For immediate effect, set RISK_KILL_SWITCH in Render env and redeploy.
    """
    os.environ["RISK_KILL_SWITCH"] = "true" if enabled else "false"
    logger.warning("Kill switch set to %s via MCP", enabled)
    return json.dumps(
        {
            "ok": True,
            "kill_switch": os.getenv("RISK_KILL_SWITCH"),
            "note": "Set in MCP process only. Restart worker to propagate.",
        }
    )
