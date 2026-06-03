"""FastAPI app entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.news_runtime import start_news_background
from api.routes import backtest, dashboard, health, market_state, models, news, signals, trades
from learning.runtime import get_learning_agent

_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = get_learning_agent()
    models.set_agents(agent, agent._registry, agent._policy)
    await start_news_background()
    yield


app = FastAPI(title="Trading AI Model", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(signals.router, prefix="/signals", tags=["signals"])
app.include_router(backtest.router, prefix="/backtest", tags=["backtest"])
app.include_router(market_state.router, prefix="/state", tags=["market_state"])
app.include_router(trades.router, prefix="/trades", tags=["trades"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(models.router)
app.include_router(news.router, prefix="/news", tags=["news"])


@app.get("/")
def root():
    return {"service": "trading-ai-model", "version": "0.1.0"}
