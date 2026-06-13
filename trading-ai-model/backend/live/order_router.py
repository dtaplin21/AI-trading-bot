"""Order router — delegates to BrokerRouter + broker adapters."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from live.broker_router import SYMBOL_BROKER, get_broker_router
from live.sync_broker import action_to_side, run_broker

logger = logging.getLogger(__name__)


class OrderRouter:
    """
    Routes orders to the correct broker based on symbol.
    In PAPER_MODE=true, logs orders without sending them.
    """

    def __init__(self) -> None:
        self.paper_mode = os.getenv("PAPER_MODE", "true").lower() == "true"
        self._order_log: list[dict] = []

    def submit(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "market",
        price: Optional[float] = None,
    ) -> dict:
        sym = symbol.upper()
        broker_name = SYMBOL_BROKER.get(sym, "unknown")
        order = {
            "symbol": sym,
            "side": side,
            "size": size,
            "type": order_type,
            "price": price,
            "broker": broker_name,
            "paper_mode": self.paper_mode,
        }

        if self.paper_mode:
            order["id"] = str(uuid.uuid4())[:8]
            order["status"] = "paper_submitted"
            self._order_log.append(order)
            logger.info(
                "OrderRouter [PAPER]: %s %s %.4f %s @ %s via %s",
                side,
                sym,
                size,
                order_type,
                price or "market",
                broker_name,
            )
            return order

        try:
            return self._route_live(order, sym, side, size, order_type, price)
        except Exception as e:
            logger.error("OrderRouter: failed to submit %s %s: %s", side, sym, e)
            order["status"] = "failed"
            order["error"] = str(e)
            return order

    def _route_live(
        self,
        order: dict,
        symbol: str,
        side: str,
        size: float,
        order_type: str,
        price: Optional[float],
    ) -> dict:
        if symbol not in SYMBOL_BROKER:
            raise NotImplementedError(f"No broker mapped for symbol: {symbol}")

        broker_side = action_to_side("buy" if side.lower() == "buy" else "sell")
        otype = "MARKET" if order_type.lower() == "market" else "LIMIT"
        broker = get_broker_router().get(symbol)
        result = run_broker(
            broker.place_order(
                symbol=symbol,
                side=broker_side,
                quantity=size,
                order_type=otype,
                limit_price=price,
            )
        )
        order["status"] = "filled" if result.status == "FILLED" else result.status.lower()
        order["order_id"] = result.broker_order_id
        if result.error_message:
            order["error"] = result.error_message
        return order

    @property
    def order_log(self) -> list[dict]:
        return list(self._order_log)
