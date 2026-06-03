"""Market News Intelligence pipeline."""

from typing import TYPE_CHECKING

__all__ = ["MarketNewsAgent"]

if TYPE_CHECKING:
    from agents.news.market_news_agent import MarketNewsAgent


def __getattr__(name: str):
    if name == "MarketNewsAgent":
        from agents.news.market_news_agent import MarketNewsAgent

        return MarketNewsAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
