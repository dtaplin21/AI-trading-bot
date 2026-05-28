"""
pipeline/confluence_report.py

The ConfluenceReport is the single structured object that the
Confluence Agent produces after reading all 13 method outputs.

This is what the ML model (LightGBM) receives as input.
This is what the MCTS planner uses to plan actions.
This is what the Audit Agent explains in plain English.

Every field is a probability or a count — never a command.
The report answers:
  - How many proven methods agree on direction?
  - What is the weighted consensus strength?
  - Where do methods conflict?
  - Does news align or fight the technical picture?
  - What is the overall confidence we should have in this setup?
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MethodVote(BaseModel):
    """One method's vote in the confluence report."""

    method_name: str
    direction: int  # +1 bullish / -1 bearish / 0 neutral
    confidence: float  # 0.0 – 1.0
    weight: float  # From agent_config.METHOD_WEIGHTS
    weighted_score: float  # direction * confidence * weight
    key_feature: str  # Most important output from this method
    is_proven: bool  # Has this method passed isolation testing?


class MethodCluster(BaseModel):
    """A group of methods that agree with each other."""

    direction: int  # +1 or -1
    methods: list[str]
    avg_confidence: float
    total_weight: float
    cluster_score: float  # avg_confidence * total_weight


class ConfluenceReport(BaseModel):
    """
    The world state brain's output for one symbol/timeframe/candle.

    This is the most important object in the entire system.
    Every downstream agent reads this — not raw method outputs.
    """

    # Identity
    symbol: str
    timeframe: str
    timestamp: datetime
    regime: str

    # Method votes (only proven methods included)
    votes: list[MethodVote] = Field(default_factory=list)
    excluded_methods: list[str] = Field(default_factory=list)  # Not proven yet

    # Direction counts
    bullish_count: int = 0  # Methods voting bullish
    bearish_count: int = 0  # Methods voting bearish
    neutral_count: int = 0  # Methods neutral/abstaining
    total_voting: int = 0  # Total proven methods that voted

    # Weighted consensus (-1.0 bearish → +1.0 bullish)
    weighted_consensus: float = 0.0
    consensus_direction: int = 0  # +1 / -1 / 0

    # Conflict
    conflict_score: float = 0.0  # 0 = full agreement, 1 = total conflict
    strongest_cluster: Optional[MethodCluster] = None
    opposing_cluster: Optional[MethodCluster] = None

    # News vs technical
    news_sentiment_score: float = 0.0  # -1.0 to +1.0
    news_aligned: bool = False
    news_conflict_score: float = 0.0  # How much news fights technical
    news_trading_blocked: bool = False
    news_risk_reason: str = ""

    # Overall confluence score (0.0 – 1.0)
    confluence_score: float = 0.0

    # Probability statement (human readable)
    probability_statement: str = ""

    # Key signals (top 3 most influential)
    top_signals: list[str] = Field(default_factory=list)

    # Flags
    min_methods_met: bool = False  # ≥3 proven methods agreed
    ready_for_prediction: bool = False  # Passes all confluence gates

    def summary(self) -> str:
        """One-line summary for logging."""
        if self.consensus_direction == 1:
            direction = "BULL"
        elif self.consensus_direction == -1:
            direction = "BEAR"
        else:
            direction = "FLAT"
        return (
            f"[{self.symbol} {self.timeframe}] "
            f"confluence={self.confluence_score:.2f} | "
            f"direction={direction} | "
            f"votes={self.bullish_count}↑ {self.bearish_count}↓ {self.neutral_count}→ | "
            f"conflict={self.conflict_score:.2f} | "
            f"news={'✓' if self.news_aligned else '✗'} | "
            f"ready={self.ready_for_prediction}"
        )
