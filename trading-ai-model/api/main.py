"""FastAPI app entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import backtest, health, market_state, signals, trades

app = FastAPI(title="Trading AI Model", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(signals.router, prefix="/signals", tags=["signals"])
app.include_router(backtest.router, prefix="/backtest", tags=["backtest"])
app.include_router(market_state.router, prefix="/state", tags=["market_state"])
app.include_router(trades.router, prefix="/trades", tags=["trades"])


@app.get("/")
def root():
    return {"service": "trading-ai-model", "version": "0.1.0"}
