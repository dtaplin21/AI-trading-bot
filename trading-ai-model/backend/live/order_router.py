"""live/order_router.py"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

SYMBOL_BROKER = {
    "MES": "tradovate",
    "ES": "tradovate",
    "MNQ": "tradovate",
    "NQ": "tradovate",
    "CL": "tradovate",
    "GC": "tradovate",
    "ZB": "tradovate",
    "RTY": "tradovate",
    "EURUSD": "oanda",
    "GBPUSD": "oanda",
    "USDJPY": "oanda",
    "USDCHF": "oanda",
    "AUDUSD": "oanda",
    "BTCUSD": "coinbase",
    "ETHUSD": "coinbase",
    "SOLUSD": "coinbase",
    "BNBUSD": "coinbase",
    "XRPUSD": "coinbase",
    "TSLA": "alpaca",
    "NVDA": "alpaca",
    "AAPL": "alpaca",
    "MSFT": "alpaca",
    "AMZN": "alpaca",
}


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
        """Route and submit an order."""
        sym = symbol.upper()
        broker = SYMBOL_BROKER.get(sym, "unknown")
        order = {
            "symbol": sym,
            "side": side,
            "size": size,
            "type": order_type,
            "price": price,
            "broker": broker,
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
                broker,
            )
            return order

        try:
            return self._route_live(order, broker)
        except Exception as e:
            logger.error("OrderRouter: failed to submit %s %s: %s", side, sym, e)
            order["status"] = "failed"
            order["error"] = str(e)
            return order

    def _route_live(self, order: dict, broker: str) -> dict:
        signal = {
            "symbol": order["symbol"],
            "action": "enter_long" if order["side"] == "buy" else "enter_short",
            "size": order["size"],
            "entry": order.get("price"),
        }
        if broker == "oanda":
            from live.oanda_executor import OandaExecutor

            return OandaExecutor().execute(signal)
        if broker == "coinbase":
            from live.coinbase_executor import CoinbaseExecutor

            return CoinbaseExecutor().execute(signal)
        raise NotImplementedError(f"No live executor for broker: {broker}")

    @property
    def order_log(self) -> list[dict]:
        return list(self._order_log)
