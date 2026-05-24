"""Learning / Retraining Agent — observe, store, label; never live-retrain."""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext


class RetrainStage(str, Enum):
    OBSERVE = "observe"
    STORE = "store"
    LABEL = "label"
    BACKTEST = "backtest"
    RETRAIN = "retrain"
    VALIDATE = "validate"
    PAPER_TEST = "paper_test"
    APPROVE = "approve"
    DEPLOY = "deploy"


SAFE_PIPELINE = [
    RetrainStage.OBSERVE,
    RetrainStage.STORE,
    RetrainStage.LABEL,
    RetrainStage.BACKTEST,
    RetrainStage.RETRAIN,
    RetrainStage.VALIDATE,
    RetrainStage.PAPER_TEST,
    RetrainStage.APPROVE,
    RetrainStage.DEPLOY,
]


class LearningAgent(BaseAgent):
    """
    Logs every prediction for scheduled retraining.
    Never promotes models live — manual approval required.
    """

    name = "learning"

    def __init__(self, log_dir: str = "./logs/training"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        row = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": ctx.symbol,
            "timeframe": ctx.timeframe,
            "signal_rank": ctx.fused.signal_rank if ctx.fused else 0,
            "methods_agreed": self._method_agreement(ctx),
            "methods_disagreed": self._method_disagreement(ctx),
            "prediction": ctx.prediction.model_dump() if ctx.prediction else None,
            "trade_plan": ctx.trade_plan.model_dump() if ctx.trade_plan else None,
            "risk_approved": ctx.risk.approved if ctx.risk else False,
            "executed": ctx.execution.executed if ctx.execution else False,
            "features": ctx.fused.features if ctx.fused else {},
            "retrain_stage": RetrainStage.OBSERVE.value,
        }
        path = self.log_dir / "observations.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        ctx.metadata["learning_logged"] = True
        return ctx

    def _method_agreement(self, ctx: PipelineContext) -> list[str]:
        agreed = []
        for o in ctx.method_outputs:
            if not o.skipped and o.confidence >= 0.55:
                agreed.append(o.method)
        return agreed

    def _method_disagreement(self, ctx: PipelineContext) -> list[str]:
        return [o.method for o in ctx.method_outputs if o.skipped or o.confidence < 0.4]
