"""
Coinbase Advanced Trade execution — live crypto only.

Gated by config.execution_config.coinbase_live_allowed() (paper off + COINBASE_LIVE_ENABLED).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from config.coinbase_symbols import is_coinbase_tradable, to_product_id
from config.execution_config import coinbase_live_allowed
from config.settings import get_settings

logger = logging.getLogger(__name__)

_client: Any = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    try:
        from coinbase.rest import RESTClient
    except ImportError as e:
        raise RuntimeError(
            "coinbase-advanced-py not installed — pip install coinbase-advanced-py"
        ) from e

    if settings.coinbase_api_key_file:
        _client = RESTClient(key_file=settings.coinbase_api_key_file)
    else:
        _client = RESTClient(
            api_key=settings.coinbase_api_key,
            api_secret=settings.coinbase_api_secret,
        )
    return _client


def reset_coinbase_client() -> None:
    global _client
    _client = None


class CoinbaseExecutor:
    """Place spot crypto orders via Coinbase Advanced Trade API."""

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

        product_id = to_product_id(symbol)
        if not product_id:
            return {"status": "error", "message": f"no product_id for {symbol}"}

        action = str(signal.get("action", "")).lower()
        quote_size = signal.get("quote_size_usd") or signal.get("notional_usd")
        if quote_size is None:
            entry = float(signal.get("entry") or 0)
            size = max(1, int(signal.get("size") or 1))
            quote_size = min(
                float(self._settings.coinbase_max_order_usd),
                entry * size if entry > 0 else self._settings.coinbase_max_order_usd,
            )
        quote_size = min(float(quote_size), float(self._settings.coinbase_max_order_usd))
        if quote_size <= 0:
            return {"status": "error", "message": "invalid quote_size"}

        client_order_id = str(uuid.uuid4())
        portfolio_id = self._settings.coinbase_portfolio_id or None

        try:
            client = _get_client()
            kwargs = {}
            if portfolio_id:
                kwargs["retail_portfolio_id"] = portfolio_id

            if "long" in action or action in ("buy", "enter_long"):
                order = client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=f"{quote_size:.2f}",
                    **kwargs,
                )
            elif "short" in action or action in ("sell", "enter_short"):
                order = client.market_order_sell(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=f"{quote_size:.2f}",
                    **kwargs,
                )
            else:
                return {"status": "error", "message": f"unsupported action {action}"}

            success = getattr(order, "success", None)
            if success is None and hasattr(order, "to_dict"):
                success = order.to_dict().get("success")
            if success:
                resp = getattr(order, "success_response", None) or {}
                if hasattr(resp, "order_id"):
                    order_id = resp.order_id
                elif isinstance(resp, dict):
                    order_id = resp.get("order_id", client_order_id)
                else:
                    order_id = client_order_id
                logger.info(
                    "CoinbaseExecutor: %s %s quote=$%.2f order_id=%s",
                    action,
                    product_id,
                    quote_size,
                    order_id,
                )
                from risk.risk_runtime import get_risk_engine

                get_risk_engine().open_position()
                return {
                    "status": "filled",
                    "broker": "coinbase",
                    "order_id": order_id,
                    "product_id": product_id,
                    "quote_size_usd": quote_size,
                }

            err = getattr(order, "error_response", None) or "order_failed"
            logger.warning("CoinbaseExecutor: order failed %s", err)
            return {"status": "rejected", "message": str(err)}

        except Exception as e:
            logger.exception("CoinbaseExecutor: %s", e)
            return {"status": "error", "message": str(e)}


_executor: Optional[CoinbaseExecutor] = None


def get_coinbase_executor() -> CoinbaseExecutor:
    global _executor
    if _executor is None:
        _executor = CoinbaseExecutor()
    return _executor
