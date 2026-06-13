"""
OANDA execution facade — delegates to live.brokers.oanda_broker.OANDABroker.

Gated by config.execution_config.oanda_live_allowed().
Kept for agents/execution_agent.py and legacy signal-dict callers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from config.execution_config import oanda_live_allowed
from config.oanda_symbols import is_oanda_tradable
from config.settings import get_settings
from live.broker_router import get_broker_router
from live.sync_broker import action_to_side, broker_order_to_result, run_broker

logger = logging.getLogger(__name__)


class OandaExecutor:
    """Sync facade over OANDABroker for legacy signal-dict API."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def can_execute(self) -> bool:
        return oanda_live_allowed(self._settings)

    def execute(self, signal: dict) -> dict:
        if not self.can_execute():
            return {"status": "blocked", "message": "oanda_live_not_enabled"}

        symbol = str(signal.get("symbol", "")).upper()
        if not is_oanda_tradable(symbol):
            return {
                "status": "skipped",
                "message": f"{symbol} not supported on OANDA (forex only)",
            }

        try:
            side = action_to_side(str(signal.get("action", "")))
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        units = int(signal.get("units") or self._settings.oanda_default_units)
        units = max(1, min(units, int(self._settings.oanda_max_units)))
        client_ref = str(uuid.uuid4())

        try:
            broker = get_broker_router().get(symbol)
            order = run_broker(
                broker.place_order(
                    symbol=symbol,
                    side=side,
                    quantity=float(units),
                    order_type="MARKET",
                    client_ref=client_ref,
                )
            )
        except Exception as exc:
            logger.exception("OandaExecutor: %s", exc)
            return {"status": "error", "message": str(exc)}

        if order.status in ("REJECTED", "ERROR"):
            return {"status": "rejected", "message": order.error_message or "order_rejected"}

        from risk.risk_runtime import get_risk_engine

        get_risk_engine().open_position()
        signed_units = units if side == "BUY" else -units
        result = broker_order_to_result(
            order,
            broker="oanda",
            instrument=symbol,
            units=signed_units,
        )
        logger.info(
            "OandaExecutor: %s %s units=%s order_id=%s",
            side,
            symbol,
            signed_units,
            result.get("order_id"),
        )
        return result


_executor: Optional[OandaExecutor] = None


def get_oanda_executor() -> OandaExecutor:
    global _executor
    if _executor is None:
        _executor = OandaExecutor()
    return _executor


def reset_oanda_executor() -> None:
    global _executor
    _executor = None
