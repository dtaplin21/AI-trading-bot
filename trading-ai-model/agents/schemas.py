"""Shared schemas for the multi-agent trading pipeline."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


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


class FusedFeatures(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    method_outputs: list[MethodOutput] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    signal_rank: int = Field(0, ge=0, le=100)
    methods_run: int = 0
    methods_skipped: int = 0


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
    fused_features: Optional[FusedFeatures] = None
    prediction: Optional[PredictionOutput] = None
    trade_plan: Optional[TradePlan] = None
    risk: Optional[RiskVerdict] = None
    execution: Optional[ExecutionResult] = None
    audit: Optional[AuditReport] = None
    llm_explanation: Optional[str] = None
    signal_rank: int = 0
    status: str = "no_action"
