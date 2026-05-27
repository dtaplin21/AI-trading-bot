"""GET /state/{symbol}."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/{symbol}")
def get_market_state(symbol: str):
    return {"symbol": symbol, "regime": "unknown", "markov_state": None}
