"""Learning / Retraining Agent — observe, store, schedule retrain; never live-retrain."""

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from agents.base import BaseAgent
from agents.news_runtime import get_news_agent
from agents.pipeline_context import PipelineContext
from config.settings import get_settings
from data.storage.timescale_store import TimescaleStore
from pipeline.world_state_runtime import get_world_state_store


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


SAFE_PIPELINE = list(RetrainStage)


class LearningAgent(BaseAgent):
    """
    Logs every prediction for scheduled retraining.
    Never promotes models live — manual approval required.
    """

    name = "learning"

    def __init__(self, log_dir: str | None = None, store: TimescaleStore | None = None, news_agent=None):
        settings = get_settings()
        self.log_dir = Path(log_dir or "./logs/training")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.store = store or TimescaleStore()
        self._news = news_agent

    @property
    def news(self):
        return self._news or get_news_agent()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "news_events_24h": [
                e.model_dump() for e in self.news.get_recent_events(ctx.symbol, hours=24)
            ],
            "retrain_stage": RetrainStage.OBSERVE.value,
        }

        path = self.log_dir / "observations.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")

        if self.store.available:
            self.store.insert_observation(
                ctx.symbol,
                ctx.timeframe,
                row["signal_rank"],
                row,
            )
            news_raw = ctx.metadata.get("news_features")
            if news_raw:
                from agents.news.news_schemas import NewsFeatures

                self.store.insert_news_feature_snapshot(
                    NewsFeatures(**news_raw),
                    ctx.symbol,
                    ctx.timeframe,
                )

        ctx.metadata["learning_logged"] = True
        ctx.metadata["retrain_due"] = self._check_retrain_due()
        if not ctx.metadata.get("world_state_stored"):
            self._store_world_state(ctx)
        return ctx

    def _store_world_state(self, ctx: PipelineContext) -> None:
        if not ctx.confluence:
            return
        store = get_world_state_store()
        ts = ctx.timestamp.isoformat().replace(":", "-")
        snapshot_id = ctx.metadata.get("world_state_snapshot_id") or f"{ctx.symbol}_{ctx.timeframe}_{ts}"
        ctx.metadata["world_state_snapshot_id"] = snapshot_id
        rank = ctx.fused.signal_rank if ctx.fused else 0
        predicted_p = 0.0
        predicted_ev = 0.0
        if ctx.prediction:
            predicted_p = float(ctx.prediction.target_before_stop_probability or 0.0)
            predicted_ev = float(ctx.prediction.expected_value or 0.0)
        store.store_snapshot(
            snapshot_id=snapshot_id,
            confluence=ctx.confluence,
            signal_rank=rank,
            predicted_p=predicted_p,
            predicted_ev=predicted_ev,
        )
        ctx.metadata["world_state_stored"] = True
        ctx.metadata["world_state_stats"] = store.stats()

    def _check_retrain_due(self) -> bool:
        try:
            from agents.learning.retrain_pipeline import RetrainPipeline

            return RetrainPipeline().due_for_retrain()
        except Exception:
            return False

    def _method_agreement(self, ctx: PipelineContext) -> list[str]:
        return [
            o.method for o in ctx.method_outputs if not o.skipped and o.confidence >= 0.55
        ]

    def _method_disagreement(self, ctx: PipelineContext) -> list[str]:
        return [o.method for o in ctx.method_outputs if o.skipped or o.confidence < 0.4]
