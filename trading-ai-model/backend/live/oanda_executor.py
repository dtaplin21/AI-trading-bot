"""
OANDA v20 REST execution — forex spot only.

Gated by config.execution_config.oanda_live_allowed() (paper off + OANDA_LIVE_ENABLED).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

import httpx

from config.execution_config import oanda_live_allowed
from config.oanda_symbols import is_oanda_tradable, to_instrument
from config.settings import get_settings

logger = logging.getLogger(__name__)

PRACTICE_BASE = "https://api-fxpractice.oanda.com"
LIVE_BASE = "https://api-fxtrade.oanda.com"
HTTP_TIMEOUT = 20.0


def oanda_api_base(settings: Any | None = None) -> str:
    settings = settings or get_settings()
    return PRACTICE_BASE if settings.oanda_practice else LIVE_BASE


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def resolve_oanda_account_id(api_key: str, base_url: str, configured_id: str) -> str | None:
    if configured_id.strip():
        return configured_id.strip()
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(
                f"{base_url}/v3/accounts",
                headers=_auth_headers(api_key),
            )
            resp.raise_for_status()
            accounts = resp.json().get("accounts") or []
            if accounts:
                return str(accounts[0].get("id", "")).strip() or None
    except Exception as exc:
        logger.warning("OandaExecutor: could not list accounts: %s", exc)
    return None


class OandaExecutor:
    """Place forex market orders via OANDA v20 API."""

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

        instrument = to_instrument(symbol)
        if not instrument:
            return {"status": "error", "message": f"no instrument for {symbol}"}

        api_key = self._settings.oanda_api_key.strip()
        base_url = oanda_api_base(self._settings)
        account_id = resolve_oanda_account_id(
            api_key, base_url, self._settings.oanda_account_id
        )
        if not account_id:
            return {
                "status": "error",
                "message": "OANDA_ACCOUNT_ID missing and account auto-discovery failed",
            }

        action = str(signal.get("action", "")).lower()
        units = int(signal.get("units") or self._settings.oanda_default_units)
        units = max(1, min(units, int(self._settings.oanda_max_units)))
        if "short" in action or action in ("sell", "enter_short"):
            units = -units

        client_order_id = str(uuid.uuid4())
        payload = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "clientExtensions": {"id": client_order_id},
            }
        }

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.post(
                    f"{base_url}/v3/accounts/{account_id}/orders",
                    headers=_auth_headers(api_key),
                    json=payload,
                )
                if resp.status_code >= 400:
                    detail = resp.text[:500]
                    logger.warning(
                        "OandaExecutor: order rejected %s %s",
                        resp.status_code,
                        detail,
                    )
                    return {"status": "rejected", "message": detail}

                body = resp.json()
                fill = body.get("orderFillTransaction") or body.get("orderCreateTransaction") or {}
                order_id = fill.get("id") or fill.get("orderID") or client_order_id
                logger.info(
                    "OandaExecutor: %s %s units=%s order_id=%s",
                    action,
                    instrument,
                    units,
                    order_id,
                )
                from risk.risk_runtime import get_risk_engine

                get_risk_engine().open_position()
                return {
                    "status": "filled",
                    "broker": "oanda",
                    "order_id": order_id,
                    "instrument": instrument,
                    "units": units,
                }
        except Exception as e:
            logger.exception("OandaExecutor: %s", e)
            return {"status": "error", "message": str(e)}


_executor: Optional[OandaExecutor] = None


def get_oanda_executor() -> OandaExecutor:
    global _executor
    if _executor is None:
        _executor = OandaExecutor()
    return _executor


def reset_oanda_executor() -> None:
    global _executor
    _executor = None
