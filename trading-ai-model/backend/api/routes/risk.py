"""Risk control API routes."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from risk.kill_switch_runtime import get_kill_switch_status, set_kill_switch_enabled
from risk.order_sizing_runtime import get_order_sizing, set_order_sizing

router = APIRouter()


class KillSwitchUpdate(BaseModel):
    enabled: bool


@router.get("/kill-switch")
def kill_switch_status():
    """Runtime kill switch state (Postgres + in-process override + env default)."""
    return get_kill_switch_status()


@router.put("/kill-switch")
async def update_kill_switch(body: KillSwitchUpdate):
    return await set_kill_switch_enabled(body.enabled)


class OrderSizingUpdate(BaseModel):
    coinbase_order_usd: float = Field(ge=1, le=10000)
    oanda_order_usd: float = Field(ge=1, le=10000)


@router.get("/order-sizing")
def order_sizing_status():
    return get_order_sizing()


@router.put("/order-sizing")
async def update_order_sizing(body: OrderSizingUpdate):
    return set_order_sizing(
        coinbase_order_usd=body.coinbase_order_usd,
        oanda_order_usd=body.oanda_order_usd,
    )
