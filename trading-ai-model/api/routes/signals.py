"""Signal routes: GET /signals, POST /analyze."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_signals():
    return {"signals": []}


@router.post("/analyze")
def analyze_symbol(symbol: str):
    return {"symbol": symbol, "status": "pending", "message": "Analysis pipeline not yet wired"}
