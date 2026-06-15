"""
Coinbase execution facade — delegates to live.brokers.coinbase_broker.CoinbaseBroker.

Gated by config.execution_config.coinbase_live_allowed().
Kept for agents/execution_agent.py and legacy signal-dict callers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from config.coinbase_symbols import is_coinbase_tradable
from config.execution_config import coinbase_live_allowed
from config.settings import get_settings
from live.broker_router import get_broker_router
from live.sync_broker import action_to_side, broker_order_to_result, run_broker
from risk.order_sizing_runtime import coinbase_order_usd

logger = logging.getLogger(__name__)


def reset_coinbase_client() -> None:
    """No-op — broker is lazy via BrokerRouter; kept for test compatibility."""


class CoinbaseExecutor:
    """Sync facade over CoinbaseBroker for legacy signal-dict API."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def can_execute(self) -> bool:
        return coinbase_live_allowed(self._settings)

    def execute(self, signal: dict) -> dict:
        if not self.can_execute():
            return {"status": "blocked", "message": "coinbase_live_not_enabled"}

        symbol = str(signal.get("symbol", "")).upper()
        if not is_coinbase_tradable(symbol):
            return {
                "status": "skipped",
                "message": f"{symbol} not supported on Coinbase (crypto only)",
            }

        try:
            side = action_to_side(str(signal.get("action", "")))
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        quote_size = signal.get("quote_size_usd") or signal.get("notional_usd")
        if quote_size is None:
            quote_size = coinbase_order_usd()
        quote_size = min(
            float(quote_size),
            coinbase_order_usd(),
            float(self._settings.coinbase_max_order_usd),
        )
        if quote_size <= 0:
            return {"status": "error", "message": "invalid quote_size"}

        entry = float(signal.get("entry") or 0)
        if entry > 0:
            quantity = quote_size / entry
        else:
            quantity = float(signal.get("size") or 0)
        if quantity <= 0:
            return {"status": "error", "message": "invalid quantity"}

        client_ref = str(uuid.uuid4())
        try:
            broker = get_broker_router().get(symbol)
            order = run_broker(
                broker.place_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_type="MARKET",
                    client_ref=client_ref,
                )
            )
        except Exception as exc:
            logger.exception("CoinbaseExecutor: %s", exc)
            return {"status": "error", "message": str(exc)}

        if order.status in ("REJECTED", "ERROR"):
            return {"status": "rejected", "message": order.error_message or "order_failed"}

        from risk.risk_runtime import get_risk_engine

        get_risk_engine().open_position()
        result = broker_order_to_result(
            order,
            broker="coinbase",
            quote_size_usd=quote_size,
        )
        logger.info(
            "CoinbaseExecutor: %s %s quote=$%.2f order_id=%s",
            side,
            symbol,
            quote_size,
            result.get("order_id"),
        )
        return result


_executor: Optional[CoinbaseExecutor] = None


def get_coinbase_executor() -> CoinbaseExecutor:
    global _executor
    if _executor is None:
        _executor = CoinbaseExecutor()
    return _executor
