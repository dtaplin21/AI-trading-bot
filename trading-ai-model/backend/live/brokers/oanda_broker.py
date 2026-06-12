"""
live/brokers/oanda_broker.py

OANDA v20 REST API adapter.
Handles: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD

OANDA instrument format: EUR_USD, GBP_USD, USD_JPY, USD_CHF, AUD_USD
Your internal symbols are translated automatically.

Required env vars:
    OANDA_API_KEY       your personal access token from OANDA hub
    OANDA_ACCOUNT_ID    your v20 account ID (e.g. 001-011-1234567-001)
    OANDA_ENVIRONMENT   'practice' | 'live'  (default: practice)

Install:  pip install oandapyV20
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from live.brokers.base_broker import AccountState, BaseBroker, BrokerOrder, BrokerPosition

logger = logging.getLogger("OANDABroker")

SYMBOL_MAP = {
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "USDCHF": "USD_CHF",
    "AUDUSD": "AUD_USD",
}


def _to_oanda(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol.upper(), symbol)


class OANDABroker(BaseBroker):
    """OANDA v20 REST adapter using oandapyV20. Supports market + limit with native TP/SL."""

    broker_id = "oanda"

    def __init__(self) -> None:
        self._api_key = os.environ["OANDA_API_KEY"]
        self._account_id = os.environ["OANDA_ACCOUNT_ID"]
        self._env = os.getenv("OANDA_ENVIRONMENT", "practice")
        self._client = self._build_client()
        logger.info("OANDABroker ready | account=%s env=%s", self._account_id, self._env)

    def _build_client(self):
        try:
            import oandapyV20

            return oandapyV20.API(access_token=self._api_key, environment=self._env)
        except ImportError as exc:
            raise ImportError("Run: pip install oandapyV20") from exc

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
        side = side.upper()
        instrument = _to_oanda(symbol)
        units = str(int(quantity)) if side == "BUY" else str(-int(quantity))

        body: dict = {
            "order": {
                "type": order_type,
                "instrument": instrument,
                "units": units,
            }
        }

        if order_type == "LIMIT" and limit_price:
            body["order"]["price"] = str(round(limit_price, 5))
            body["order"]["timeInForce"] = "GTC"

        if tp_price:
            body["order"]["takeProfitOnFill"] = {"price": str(round(tp_price, 5))}
        if sl_price:
            body["order"]["stopLossOnFill"] = {"price": str(round(sl_price, 5))}

        if client_ref:
            body["order"]["clientExtensions"] = {"id": client_ref[:128]}

        def _post() -> dict:
            import oandapyV20.endpoints.orders as orders_ep

            req = orders_ep.OrderCreate(self._account_id, data=body)
            return self._client.request(req)

        try:
            resp = await asyncio.to_thread(_post)
            filled = resp.get("orderFillTransaction") or {}
            created = resp.get("orderCreateTransaction") or {}
            broker_id = filled.get("id") or created.get("id") or "unknown"
            status = "FILLED" if filled else "PENDING"
            fill_px = float(filled.get("price", 0)) or None

            logger.info(
                "OANDA order | id=%s status=%s fill=%.5f",
                broker_id,
                status,
                fill_px or 0,
            )

            return BrokerOrder(
                broker_order_id=str(broker_id),
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                status=status,
                filled_price=fill_px,
                raw_response=resp,
            )
        except Exception as exc:
            logger.error("OANDA place_order failed: %s", exc)
            return BrokerOrder(
                "",
                symbol,
                side,
                quantity,
                order_type,
                "REJECTED",
                error_message=str(exc),
            )

    async def cancel_order(self, broker_order_id: str) -> bool:
        def _cancel() -> None:
            import oandapyV20.endpoints.orders as orders_ep

            req = orders_ep.OrderCancel(self._account_id, orderID=broker_order_id)
            self._client.request(req)

        try:
            await asyncio.to_thread(_cancel)
            logger.info("OANDA cancel | id=%s", broker_order_id)
            return True
        except Exception as exc:
            logger.error("OANDA cancel_order failed: %s", exc)
            return False

    async def close_position(
        self,
        symbol: str,
        quantity: Optional[float] = None,
    ) -> BrokerOrder:
        instrument = _to_oanda(symbol)

        def _close() -> dict:
            import oandapyV20.endpoints.trades as trades_ep

            req = trades_ep.TradesList(
                self._account_id,
                params={"instrument": instrument, "state": "OPEN"},
            )
            resp = self._client.request(req)
            trades = resp.get("trades", [])
            if not trades:
                raise ValueError("no_open_trade")

            trade_id = trades[0]["id"]
            data: dict = {}
            if quantity:
                data["units"] = str(int(quantity))

            close_req = trades_ep.TradeClose(self._account_id, tradeID=trade_id, data=data)
            return self._client.request(close_req)

        try:
            close_resp = await asyncio.to_thread(_close)
            tx = close_resp.get("orderFillTransaction", {})
            return BrokerOrder(
                broker_order_id=str(tx.get("id", "unknown")),
                symbol=symbol,
                side="SELL",
                quantity=abs(float(tx.get("units", 0))),
                order_type="MARKET",
                status="FILLED",
                filled_price=float(tx.get("price", 0)) or None,
                raw_response=close_resp,
            )
        except ValueError:
            return BrokerOrder(
                "",
                symbol,
                "SELL",
                0,
                "MARKET",
                "REJECTED",
                error_message="no_open_trade",
            )
        except Exception as exc:
            logger.error("OANDA close_position failed: %s", exc)
            return BrokerOrder(
                "",
                symbol,
                "SELL",
                0,
                "MARKET",
                "REJECTED",
                error_message=str(exc),
            )

    async def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        instrument = _to_oanda(symbol)

        def _fetch() -> Optional[BrokerPosition]:
            import oandapyV20.endpoints.positions as pos_ep

            req = pos_ep.PositionDetails(self._account_id, instrument=instrument)
            resp = self._client.request(req)
            pos = resp.get("position", {})
            long_units = int(pos.get("long", {}).get("units", 0))
            short_units = int(pos.get("short", {}).get("units", 0))
            if long_units == 0 and short_units == 0:
                return None
            side = "LONG" if long_units > 0 else "SHORT"
            qty = abs(long_units or short_units)
            avg = float(pos.get(side.lower(), {}).get("averagePrice", 0))
            upnl = float(pos.get("unrealizedPL", 0))
            return BrokerPosition(
                broker_position_id=instrument,
                symbol=symbol,
                side=side,
                quantity=qty,
                entry_price=avg,
                current_price=avg,
                unrealized_pnl=upnl,
            )

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.error("OANDA get_position failed: %s", exc)
            return None

    async def get_account(self) -> AccountState:
        def _fetch() -> AccountState:
            import oandapyV20.endpoints.accounts as acct_ep

            req = acct_ep.AccountSummary(self._account_id)
            resp = self._client.request(req)
            acct = resp.get("account", {})
            return AccountState(
                account_id=self._account_id,
                cash_balance=float(acct.get("balance", 0)),
                buying_power=float(acct.get("marginAvailable", 0)),
                unrealized_pnl=float(acct.get("unrealizedPL", 0)),
                realized_pnl_day=float(acct.get("pl", 0)),
                currency=acct.get("currency", "USD"),
            )

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.error("OANDA get_account failed: %s", exc)
            return AccountState(self._account_id, 0, 0, 0, 0)
