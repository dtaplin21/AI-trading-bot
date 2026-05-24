"""Signal routes: GET /signals, POST /analyze."""

from fastapi import APIRouter, Query

from agents.supervisor import TradingSupervisor
from data.loaders.market_data_loader import MarketDataLoader
from tests.fixtures.sample_ohlcv import sample_ohlcv

router = APIRouter()
_supervisor = TradingSupervisor()


@router.get("")
def list_signals():
    return {"signals": [], "message": "Use POST /signals/analyze to run the multi-agent pipeline"}


@router.post("/analyze")
def analyze_symbol(
    symbol: str = Query(..., description="Futures symbol e.g. MES"),
    timeframe: str = Query("5m"),
    historical_sample_size: int = Query(1420, ge=0),
    execute: bool = Query(False, description="Paper execute if risk approved"),
):
    loader = MarketDataLoader()
    ohlcv = loader.load(symbol, start="", end="")
    if ohlcv.empty:
        ohlcv = sample_ohlcv(60)

    decision = _supervisor.process_candle(
        symbol=symbol.upper(),
        ohlcv=ohlcv,
        timeframe=timeframe,
        historical_sample_size=historical_sample_size,
        execute=execute,
    )

    return {
        "decision": decision.model_dump(),
        "explanation": _supervisor.explain_last(decision),
    }
