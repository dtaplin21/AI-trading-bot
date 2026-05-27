"""Combines price action signals into confluence scores."""

from dataclasses import dataclass


@dataclass
class PriceActionConfluence:
    price_action_confluence_score: float
    reversal_confluence_score: float
    continuation_confluence_score: float
    avoid_trade_score: float


class PriceActionConfluenceService:
    def score(self, layers: dict) -> PriceActionConfluence:
        vals = list(layers.values()) or [0.0]
        avg = sum(vals) / len(vals)
        return PriceActionConfluence(avg, avg * 0.9, avg * 0.8, 1.0 - avg)

