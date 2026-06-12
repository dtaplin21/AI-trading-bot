"""Tests for BrokerRouter symbol mapping and lazy init."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live.broker_router import SYMBOL_BROKER, BrokerRouter, get_broker_router


def _mock_broker(broker_id: str) -> MagicMock:
    broker = MagicMock()
    broker.broker_id = broker_id
    broker.get_account = AsyncMock(
        return_value=MagicMock(cash_balance=10_000.0, buying_power=10_000.0, realized_pnl_day=0.0)
    )
    broker.place_order = AsyncMock(
        return_value=MagicMock(
            status="PENDING",
            broker_order_id="x",
            filled_price=None,
            error_message="",
        )
    )
    return broker


@pytest.fixture
def router():
    r = BrokerRouter()
    with patch.object(r, "_init_broker", side_effect=lambda name: _mock_broker(name)):
        yield r


def test_symbol_broker_mapping():
    assert SYMBOL_BROKER["BTCUSD"] == "coinbase"
    assert SYMBOL_BROKER["EURUSD"] == "oanda"
    assert SYMBOL_BROKER["MES"] == "webull"
    assert SYMBOL_BROKER["TSLA"] == "webull"


def test_get_unknown_symbol_raises():
    r = BrokerRouter()
    with pytest.raises(ValueError, match="No broker mapped"):
        r.get("UNKNOWN")


def test_broker_name():
    r = BrokerRouter()
    assert r.broker_name("eurusd") == "oanda"
    assert r.broker_name("XYZ") == "unknown"


def test_lazy_singleton_per_broker(router):
    b1 = router.get("BTCUSD")
    b2 = router.get("ETHUSD")
    assert b1 is b2
    assert router.get("EURUSD").broker_id == "oanda"


def test_get_broker_router_singleton():
    r1 = get_broker_router()
    r2 = get_broker_router()
    assert r1 is r2


@pytest.mark.asyncio
async def test_broker_get_account(router):
    broker = router.get("MES")
    acct = await broker.get_account()
    assert acct.cash_balance == 10_000.0
