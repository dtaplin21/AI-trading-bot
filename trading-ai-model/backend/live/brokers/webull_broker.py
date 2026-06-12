"""Webull broker adapter — futures and US equities."""

from __future__ import annotations

import logging
import os
from typing import Optional

from config.settings import get_settings
from live.brokers.base_broker import AccountState, BaseBroker, BrokerOrder, BrokerPosition

logger = logging.getLogger(__name__)


class WebullBroker(BaseBroker):
    """Webull adapter for futures and equities. Validates credentials at init."""

    broker_id = "webull"

    def __init__(self) -> None:
        settings = get_settings()
        if not (settings.webull_app_key.strip() and settings.webull_app_secret.strip()):
            raise RuntimeError(
                "WebullBroker: WEBULL_APP_KEY and WEBULL_APP_SECRET required"
            )
        self._app_key = settings.webull_app_key.strip()
        logger.info("WebullBroker initialized (credentials present)")

    async def get_account(self) -> AccountState:
        fallback = float(os.getenv("ACCOUNT_SIZE", "10000"))
        return AccountState("webull", fallback, fallback)

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        client_ref: Optional[str] = None,
    ) -> BrokerOrder:
        self._log_order("PLACE", symbol, side, quantity, limit_price)
        return BrokerOrder(
            broker_order_id="",
            symbol=symbol,
            side=side.upper(),
            quantity=quantity,
            order_type=order_type,
            status="REJECTED",
            error_message=(
                f"WebullBroker: live order API for {symbol} not yet implemented — "
                "credentials validated at startup"
            ),
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        logger.warning("WebullBroker cancel_order not implemented | id=%s", broker_order_id)
        return False

    async def close_position(
        self,
        symbol: str,
        quantity: Optional[float] = None,
    ) -> BrokerOrder:
        return BrokerOrder("", symbol, "SELL", 0, "MARKET", "REJECTED", error_message="not_implemented")

    async def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        return None
