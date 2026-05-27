"""Pipeline context passed between agents."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from agents.schemas import (
    AuditReport,
    ChartStructure,
    ExecutionResult,
    FusedFeatures,
    MethodOutput,
    PipelineDecision,
    PredictionOutput,
    RiskVerdict,
    TradePlan,
)
from pipeline.confluence_report import ConfluenceReport
from risk.risk_engine import PortfolioState


@dataclass
class PipelineContext:
    symbol: str
    timeframe: str
    ohlcv: pd.DataFrame
    timestamp: datetime = field(default_factory=datetime.utcnow)
    swings: list[tuple[int, float]] = field(default_factory=list)
    chart: Optional[ChartStructure] = None
    method_outputs: list[MethodOutput] = field(default_factory=list)
    confluence: Optional[ConfluenceReport] = None
    fused: Optional[FusedFeatures] = None
    prediction: Optional[PredictionOutput] = None
    trade_plan: Optional[TradePlan] = None
    risk: Optional[RiskVerdict] = None
    execution: Optional[ExecutionResult] = None
    audit: Optional[AuditReport] = None
    portfolio: PortfolioState = field(default_factory=PortfolioState)
    historical_sample_size: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_decision(self) -> PipelineDecision:
        return PipelineDecision(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=self.timestamp,
            chart=self.chart,
            fused_features=self.fused,
            prediction=self.prediction,
            trade_plan=self.trade_plan,
            risk=self.risk,
            execution=self.execution,
            audit=self.audit,
            llm_explanation=self.metadata.get("llm_explanation"),
            signal_rank=self.fused.signal_rank if self.fused else 0,
            status=self._status(),
        )

    def _status(self) -> str:
        if self.execution and self.execution.executed:
            return "executed"
        if self.risk and not self.risk.approved:
            return "risk_rejected"
        if self.prediction and self.prediction.should_avoid:
            return "avoid"
        if self.prediction and self.prediction.should_wait:
            return "wait"
        if self.trade_plan and self.trade_plan.action.value not in ("wait", "do_nothing"):
            return "paper_trade_candidate"
        return "no_action"
