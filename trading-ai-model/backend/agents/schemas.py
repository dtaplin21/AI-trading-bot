"""Shared schemas for the multi-agent trading pipeline."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.news.news_schemas import NewsFeatures


class NewsFeatureBlock(BaseModel):
    """News Intelligence fields on FusedFeatures / FusedFeatureSet."""

    news_sentiment_score: float = 0.0
    news_impact_score: float = 0.0
    news_urgency_score: float = 0.0
    volatility_risk_score: float = 0.0
    minutes_since_last_news: float = 9999.0
    minutes_until_next_event: float = 9999.0
    high_impact_news_active: bool = False
    breaking_news_active: bool = False
    affected_symbol_match: bool = False
    news_conflict_score: float = 0.0
    news_trading_blocked: bool = False
    news_reduce_size: bool = False
    news_manual_required: bool = False
    news_risk_reason: str = ""

    @classmethod
    def news_fields_from(cls, news: NewsFeatures) -> dict:
        return {
            "news_sentiment_score": news.news_sentiment_score,
            "news_impact_score": news.news_impact_score,
            "news_urgency_score": news.news_urgency_score,
            "volatility_risk_score": news.volatility_risk_score,
            "minutes_since_last_news": news.minutes_since_last_news,
            "minutes_until_next_event": news.minutes_until_next_event,
            "high_impact_news_active": news.high_impact_news_active,
            "breaking_news_active": news.breaking_news_active,
            "affected_symbol_match": news.affected_symbol_match,
            "news_conflict_score": news.news_conflict_score,
            "news_trading_blocked": news.trading_blocked,
            "news_reduce_size": news.reduce_size_recommended,
            "news_manual_required": news.manual_approval_required,
            "news_risk_reason": news.news_risk_reason or "",
        }


class TradeAction(str, Enum):
    WAIT = "wait"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    SCALE_IN = "scale_in"
    PARTIAL_PROFIT = "partial_profit"
    TRAIL_STOP = "trail_stop"
    EXIT = "exit"
    DO_NOTHING = "do_nothing"


class MethodOutput(BaseModel):
    method: str
    features: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    skipped: bool = False
    skip_reason: Optional[str] = None


class ChartStructure(BaseModel):
    swing_highs: list[float] = Field(default_factory=list)
    swing_lows: list[float] = Field(default_factory=list)
    trend_direction: str = "unknown"
    range_zones: list[tuple[float, float]] = Field(default_factory=list)
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    higher_highs: bool = False
    higher_lows: bool = False
    lower_highs: bool = False
    lower_lows: bool = False
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    vwap_relation: str = "unknown"


class FusedFeatures(NewsFeatureBlock):
    symbol: str
    timeframe: str
    timestamp: datetime
    method_outputs: list[MethodOutput] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    signal_rank: int = Field(0, ge=0, le=100)
    methods_run: int = 0
    methods_skipped: int = 0
    news: NewsFeatures = Field(default_factory=NewsFeatures)

    @classmethod
    def with_news(cls, news: NewsFeatures, **kwargs) -> "FusedFeatures":
        """Build FusedFeatures with typed news block populated."""
        return cls(**kwargs, news=news, **cls.news_fields_from(news))


class PredictionOutput(BaseModel):
    should_start: bool = False
    should_stop: bool = False
    should_wait: bool = True
    should_avoid: bool = False
    target_before_stop_probability: float = 0.0
    reversal_probability: float = 0.0
    continuation_probability: float = 0.0
    expected_value: float = 0.0
    expected_drawdown: float = 0.0
    model_confidence: float = 0.0
    model_version: str = "rule_v0"


class TradePlan(BaseModel):
    action: TradeAction = TradeAction.WAIT
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    stop_limit: Optional[float] = None
    start_condition: Optional[str] = None
    stop_condition: Optional[str] = None
    wait_condition: Optional[str] = None
    scale_in_condition: Optional[str] = None
    exit_condition: Optional[str] = None
    mcts_path: list[str] = Field(default_factory=list)


class RiskVerdict(BaseModel):
    approved: bool = False
    reason: Optional[str] = None
    max_position_size: float = 0.0
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    executed: bool = False
    mode: str = "paper"
    order_id: Optional[str] = None
    message: Optional[str] = None


class AuditReport(BaseModel):
    summary: str
    reasons: list[str] = Field(default_factory=list)
    method_agreement: dict[str, bool] = Field(default_factory=dict)
    disagreements: list[str] = Field(default_factory=list)


class PipelineDecision(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    chart: Optional[ChartStructure] = None
    confluence: Optional[Any] = None  # ConfluenceReport — Any avoids circular import
    fused_features: Optional[FusedFeatures] = None
    prediction: Optional[PredictionOutput] = None
    trade_plan: Optional[TradePlan] = None
    risk: Optional[RiskVerdict] = None
    execution: Optional[ExecutionResult] = None
    audit: Optional[AuditReport] = None
    llm_explanation: Optional[str] = None
    signal_rank: int = 0
    status: str = "no_action"
