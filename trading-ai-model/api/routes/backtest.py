"""POST /backtest."""

from fastapi import APIRouter

router = APIRouter()


@router.post("")
def run_backtest(symbol: str, start: str, end: str):
    return {"symbol": symbol, "start": start, "end": end, "status": "pending"}
