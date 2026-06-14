"""GET /trades — closed trade history for the dashboard."""

from fastapi import APIRouter, Query

from api.services.trades_service import build_closed_trades

router = APIRouter()


@router.get("")
def list_trades(limit: int = Query(default=100, ge=1, le=500)):
    return build_closed_trades(limit=limit)
