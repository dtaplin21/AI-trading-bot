"""Confluence report schemas — unified world state for downstream agents."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MethodVote(BaseModel):
    method_name: str
    direction: int = Field(description="+1 bullish, -1 bearish, 0 neutral/confirming")
    confidence: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0)
    weighted_score: float
    key_feature: str
    is_proven: bool = True


class MethodCluster(BaseModel):
    direction: int
    methods: list[str]
    avg_confidence: float
    total_weight: float
    cluster_score: float


class ConfluenceReport(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    regime: str

    votes: list[MethodVote] = Field(default_factory=list)
    excluded_methods: list[str] = Field(default_factory=list)

    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    total_voting: int = 0

    weighted_consensus: float = 0.0
    consensus_direction: int = 0
    conflict_score: float = 0.0

    strongest_cluster: Optional[MethodCluster] = None
    opposing_cluster: Optional[MethodCluster] = None

    news_sentiment_score: float = 0.0
    news_aligned: bool = True
    news_conflict_score: float = 0.0
    news_trading_blocked: bool = False
    news_risk_reason: str = ""

    confluence_score: float = Field(ge=0.0, le=1.0, default=0.0)
    probability_statement: str = ""
    top_signals: list[str] = Field(default_factory=list)

    min_methods_met: bool = False
    ready_for_prediction: bool = False

    def summary(self) -> str:
        dir_label = {1: "bullish", -1: "bearish", 0: "neutral"}.get(self.consensus_direction, "neutral")
        return (
            f"{self.symbol} {self.timeframe} | confluence={self.confluence_score:.2f} | "
            f"{dir_label} | {self.bullish_count}↑ {self.bearish_count}↓ {self.neutral_count}○ | "
            f"ready={self.ready_for_prediction}"
        )

    def direction_label(self) -> str:
        return {1: "long", -1: "short", 0: "flat"}.get(self.consensus_direction, "flat")
