"""Risk control API routes."""

from fastapi import APIRouter
from pydantic import BaseModel

from risk.kill_switch_runtime import get_kill_switch_status, set_kill_switch_enabled

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
