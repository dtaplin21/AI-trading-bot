"""Base broker adapter interface for live execution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AccountState:
    account_id: str
    cash_balance: float
    buying_power: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl_day: float = 0.0
    currency: str = "USD"


@dataclass
class BrokerOrder:
    broker_order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    status: str  # FILLED | PENDING | SUBMITTED | REJECTED | ERROR
    filled_price: Optional[float] = None
    error_message: str = ""
    raw_response: Optional[dict[str, Any]] = field(default=None, repr=False)


@dataclass
class BrokerPosition:
    broker_position_id: str
    symbol: str
    side: str  # LONG | SHORT
    quantity: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0


class BaseBroker(ABC):
    broker_id: str = "base"

    def _log_order(
        self,
        action: str,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: Optional[float] = None,
    ) -> None:
        logger.info(
            "%s | %s %s %s qty=%s limit=%s",
            action,
            self.broker_id,
            symbol,
            side,
            quantity,
            limit_price,
        )

    @abstractmethod
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
        ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        ...

    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        quantity: Optional[float] = None,
    ) -> BrokerOrder:
        ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        ...

    @abstractmethod
    async def get_account(self) -> AccountState:
        ...
