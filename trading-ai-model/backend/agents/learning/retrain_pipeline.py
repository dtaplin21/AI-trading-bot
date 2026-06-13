"""API/cron facade over ml.training.retrain_pipeline."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from agents.learning.model_registry import ModelStage
from config.settings import get_settings
from ml.promotion.promotion_policy import PromotionPolicy
from ml.registry.model_registry import ModelRegistry
from ml.training.retrain_pipeline import RetrainPipeline as UnifiedRetrainPipeline
from ml.training.retrain_pipeline import RetrainResult
from pipeline.world_state_runtime import get_world_state_store

logger = logging.getLogger(__name__)


class RetrainPipeline:
    """Scheduled retrain + manual promote/rollback for /models API."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.registry = ModelRegistry()
        self.policy = PromotionPolicy()
        self._inner = UnifiedRetrainPipeline(
            get_world_state_store(),
            self.registry,
            self.policy,
        )
        self.state_file = Path(self.settings.model_dir) / "retrain_state.json"

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"last_retrain": None, "last_evaluation": None}

    def _save_state(self, state: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2, default=str))

    def due_for_retrain(self) -> bool:
        state = self._load_state()
        last = state.get("last_retrain")
        if not last:
            return True
        last_dt = datetime.fromisoformat(last)
        return datetime.now(timezone.utc) - last_dt >= timedelta(
            days=self.settings.retrain_schedule_days
        )

    def run_scheduled_retrain(self, force: bool = False) -> dict:
        if not force and not self.due_for_retrain():
            return {
                "status": "skipped",
                "reason": "not_due",
                "next_in_days": self.settings.retrain_schedule_days,
            }

        result = self._inner.run(force=force, requested_by="scheduled")
        return self._finalize(result)

    def run(self, force: bool = False, requested_by: str = "api") -> dict:
        """Direct run — same as unified pipeline."""
        result = self._inner.run(force=force, requested_by=requested_by)
        return self._finalize(result)

    def _finalize(self, result: RetrainResult) -> dict:
        if not result.skipped and not result.error:
            state = self._load_state()
            state["last_retrain"] = datetime.now(timezone.utc).isoformat()
            if result.model_id:
                state["last_candidate_id"] = result.model_id
            if result.decision:
                state["last_promotion_decision"] = result.decision.to_dict()
            self._save_state(state)

        payload = result.to_dict()
        if result.skipped:
            payload["status"] = "skipped"
        elif result.error:
            payload["status"] = "error"
        else:
            payload["status"] = "retrained"
            rec = self.registry.get_model(result.model_id) if result.model_id else None
            payload["model"] = rec.to_dict() if rec else None
            payload["registry"] = self.registry.status_summary()
        return payload

    def evaluate_candidate_metrics(self, metrics: dict, model_id: str) -> dict:
        decision = self.policy.evaluate(
            model_id=model_id,
            n_samples=int(metrics.get("samples", 0)),
            holdout_brier=float(metrics.get("holdout_brier", 1.0)),
            production_brier=float(metrics.get("production_brier", 1.0)),
            holdout_auc=float(metrics.get("holdout_auc", 0.0)),
            positive_rate=float(metrics.get("positive_rate", 0.5)),
            walk_forward_brier=metrics.get("walk_forward_brier"),
        )
        return decision.to_dict()

    def approve_for_paper_test(self, model_id: str) -> dict:
        rec = self.registry.set_status(model_id, ModelStage.PAPER_TEST.value)
        if not rec:
            raise KeyError(f"Model not found: {model_id}")
        return rec.to_dict()

    def approve_model(self, model_id: str) -> dict:
        rec = self.registry.set_status(model_id, ModelStage.APPROVED.value)
        if not rec:
            raise KeyError(f"Model not found: {model_id}")
        return rec.to_dict()

    def promote_model(self, model_id: str, approved_by: str) -> dict:
        decision = self.policy.manual_approve(
            model_id, approved_by, notes="API /models/{id}/promote"
        )
        rec = self.registry.get_model(model_id)
        if not rec:
            raise KeyError(f"Model not found: {model_id}")

        if rec.status != ModelStage.PRODUCTION.value:
            ok = self.registry.promote_to_production(
                model_id,
                decision.promoted_by,
                gate_results=[],
                notes=decision.notes,
            )
            if not ok:
                raise RuntimeError(f"Promotion failed for {model_id}")
            self._inner._reload_prediction_models()

        updated = self.registry.get_model(model_id)
        if updated is None:
            raise KeyError(f"Model not found: {model_id}")
        out = updated.to_dict()
        out["promotion_decision"] = decision.to_dict()
        return out

    def rollback_model(self, model_id: str, rolled_back_by: str) -> dict:
        ok = self.registry.rollback(model_id, rolled_back_by)
        if not ok:
            raise ValueError(f"Rollback failed for {model_id}")
        self._inner._reload_prediction_models()
        rec = self.registry.get_model(model_id)
        return rec.to_dict() if rec else {}
