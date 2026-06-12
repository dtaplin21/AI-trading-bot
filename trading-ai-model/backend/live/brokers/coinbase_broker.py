"""
live/brokers/coinbase_broker.py

Coinbase Advanced Trade REST API adapter.
Handles: BTCUSD, ETHUSD, SOLUSD, BNBUSD, XRPUSD

Coinbase product ID format: BTC-USD, ETH-USD, SOL-USD, BNB-USD, XRP-USD
Your internal symbols (BTCUSD) are translated automatically.

Required env vars:
    COINBASE_API_KEY        your API key name (starts with "organizations/...")
    COINBASE_API_SECRET     your EC private key (PEM format, one line with \\n)

Coinbase Advanced Trade uses JWT authentication (not HMAC).
The requests-based auth is handled via the coinbase-advanced-py SDK.

Install:  pip install coinbase-advanced-py
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from live.brokers.base_broker import BaseBroker, AccountState, BrokerOrder, BrokerPosition

logger = logging.getLogger("CoinbaseBroker")

SYMBOL_MAP = {
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "BNBUSD": "BNB-USD",
    "XRPUSD": "XRP-USD",
}


def _to_coinbase(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol.upper(), symbol)


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(getattr(obj, "__dict__", {}) or {})


def _gen_ref() -> str:
    import uuid

    return f"bot-{uuid.uuid4().hex[:12]}"


class CoinbaseBroker(BaseBroker):
    """Coinbase Advanced Trade adapter. Uses coinbase-advanced-py SDK for JWT auth."""

    broker_id = "coinbase"

    def __init__(self) -> None:
        self._api_key = os.environ["COINBASE_API_KEY"]
        self._api_secret = os.environ["COINBASE_API_SECRET"].replace("\\n", "\n")
        self._client = self._build_client()
        logger.info("CoinbaseBroker ready | key=%s...", self._api_key[:20])

    def _build_client(self):
        try:
            from coinbase.rest import RESTClient

            return RESTClient(api_key=self._api_key, api_secret=self._api_secret)
        except ImportError as exc:
            raise ImportError("Run: pip install coinbase-advanced-py") from exc

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
        product_id = _to_coinbase(symbol)
        side = side.upper()

        def _post() -> dict[str, Any]:
            ref = client_ref or _gen_ref()
            if order_type == "MARKET":
                if side == "BUY":
                    return _as_dict(
                        self._client.market_order_buy(
                            client_order_id=ref,
                            product_id=product_id,
                            base_size=str(quantity),
                        )
                    )
                return _as_dict(
                    self._client.market_order_sell(
                        client_order_id=ref,
                        product_id=product_id,
                        base_size=str(quantity),
                    )
                )
            if side == "BUY":
                return _as_dict(
                    self._client.limit_order_gtc_buy(
                        client_order_id=ref,
                        product_id=product_id,
                        base_size=str(quantity),
                        limit_price=str(limit_price),
                    )
                )
            return _as_dict(
                self._client.limit_order_gtc_sell(
                    client_order_id=ref,
                    product_id=product_id,
                    base_size=str(quantity),
                    limit_price=str(limit_price),
                )
            )

        try:
            resp = await asyncio.to_thread(_post)
            order = resp.get("success_response") or resp
            if not isinstance(order, dict):
                order = _as_dict(order)
            broker_id = order.get("order_id", "unknown")
            status = "FILLED" if order.get("status") == "FILLED" else "PENDING"

            logger.info("Coinbase order placed | id=%s status=%s", broker_id, status)

            if tp_price or sl_price:
                logger.info(
                    "TP=%.5f SL=%.5f will be managed by LivePositionMonitor (Coinbase no bracket)",
                    tp_price or 0,
                    sl_price or 0,
                )

            filled = order.get("average_filled_price")
            return BrokerOrder(
                broker_order_id=str(broker_id),
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                status=status,
                filled_price=float(filled) if filled else None,
                raw_response=order,
            )
        except Exception as exc:
            logger.error("Coinbase place_order failed: %s", exc)
            return BrokerOrder(
                broker_order_id="",
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                status="REJECTED",
                error_message=str(exc),
            )

    async def cancel_order(self, broker_order_id: str) -> bool:
        def _cancel() -> None:
            self._client.cancel_orders(order_ids=[broker_order_id])

        try:
            await asyncio.to_thread(_cancel)
            logger.info("Coinbase cancel | id=%s", broker_order_id)
            return True
        except Exception as exc:
            logger.error("Coinbase cancel_order failed: %s", exc)
            return False

    async def close_position(
        self,
        symbol: str,
        quantity: Optional[float] = None,
    ) -> BrokerOrder:
        pos = await self.get_position(symbol)
        if pos is None:
            logger.warning("close_position: no open position for %s", symbol)
            return BrokerOrder(
                "",
                symbol,
                "SELL",
                0,
                "MARKET",
                "REJECTED",
                error_message="no_position",
            )
        close_side = "SELL" if pos.side == "LONG" else "BUY"
        qty = quantity or pos.quantity
        return await self.place_order(symbol, close_side, qty, "MARKET")

    async def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        product_id = _to_coinbase(symbol)
        asset = product_id.split("-")[0]

        def _fetch() -> Optional[BrokerPosition]:
            port = _as_dict(self._client.get_portfolios())
            for p in port.get("portfolios") or []:
                if p.get("type") != "DEFAULT":
                    continue
                pid = p["uuid"]
                breakdown = _as_dict(self._client.get_portfolio_breakdown(portfolio_uuid=pid))
                for pos in breakdown.get("breakdown", {}).get("spot_positions") or []:
                    if pos.get("asset") != asset:
                        continue
                    qty = float(pos.get("total_balance_crypto", 0))
                    if qty <= 0:
                        continue
                    avg = float(pos.get("average_entry_price", {}).get("value", 0))
                    cur = float(pos.get("current_price", {}).get("value", 0))
                    upnl = float(pos.get("unrealized_pnl", {}).get("value", 0))
                    return BrokerPosition(
                        broker_position_id=pid,
                        symbol=symbol,
                        side="LONG",
                        quantity=qty,
                        entry_price=avg,
                        current_price=cur,
                        unrealized_pnl=upnl,
                    )
            return None

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.error("Coinbase get_position failed: %s", exc)
            return None

    async def get_account(self) -> AccountState:
        def _fetch() -> AccountState:
            accounts = _as_dict(self._client.get_accounts())
            usd_cash = 0.0
            for acct in accounts.get("accounts") or []:
                if acct.get("currency") == "USD":
                    usd_cash += float(acct.get("available_balance", {}).get("value", 0))
            return AccountState(
                account_id="coinbase",
                cash_balance=usd_cash,
                buying_power=usd_cash,
            )

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.error("Coinbase get_account failed: %s", exc)
            return AccountState("coinbase", 0, 0, 0, 0)
