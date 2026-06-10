"""live/execution_monitor.py"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class ExecutionMonitor:
    """
    Monitors open orders and detects fills.
    Notifies the PaperTrader/LiveTrader when orders are filled.
    """

    def __init__(self) -> None:
        self._open_orders: dict[str, dict] = {}
        self._filled: list[dict] = []

    def register_order(self, order_id: str, order: dict) -> None:
        self._open_orders[order_id] = {
            **order,
            "registered_at": datetime.now(tz=timezone.utc).isoformat(),
            "status": "pending",
        }
        logger.info(
            "ExecutionMonitor: registered order %s %s %s @ %s",
            order_id,
            order.get("side"),
            order.get("symbol"),
            order.get("price"),
        )

    def check_fills(self, current_prices: dict) -> list[dict]:
        """
        Check if any open orders have been filled based on current prices.
        Returns list of filled order dicts.
        """
        newly_filled: list[dict] = []
        for order_id, order in list(self._open_orders.items()):
            symbol = order.get("symbol")
            price = current_prices.get(symbol)
            if price is None:
                continue

            order_price = float(order.get("price", 0))
            side = order.get("side", "buy")
            order_type = order.get("type", "limit")

            filled = False
            if order_type == "market":
                filled = True
            elif order_type == "limit":
                if side == "buy" and price <= order_price:
                    filled = True
                if side == "sell" and price >= order_price:
                    filled = True
            elif order_type == "stop":
                if side == "buy" and price >= order_price:
                    filled = True
                if side == "sell" and price <= order_price:
                    filled = True

            if filled:
                order["status"] = "filled"
                order["fill_price"] = price
                order["filled_at"] = datetime.now(tz=timezone.utc).isoformat()
                self._filled.append(order)
                del self._open_orders[order_id]
                newly_filled.append(order)
                logger.info("ExecutionMonitor: filled %s @ %.5f", order_id, price)

        return newly_filled

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._open_orders:
            del self._open_orders[order_id]
            logger.info("ExecutionMonitor: cancelled %s", order_id)
            return True
        return False

    @property
    def open_count(self) -> int:
        return len(self._open_orders)

    @property
    def filled_count(self) -> int:
        return len(self._filled)
