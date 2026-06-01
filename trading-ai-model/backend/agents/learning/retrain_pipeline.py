"""Safe retraining pipeline — PromotionPolicy + ml.registry.ModelRegistry."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from agents.learning.model_registry import ModelStage
from config.settings import get_settings
from ml.promotion.promotion_policy import PromotionPolicy
from ml.registry.model_registry import ModelRegistry
from ml.training.train_lightgbm import train

logger = logging.getLogger(__name__)


class RetrainPipeline:
    """Observe → Store → Label → Retrain → Validate → Approve → Deploy."""

    def __init__(self):
        self.settings = get_settings()
        self.registry = ModelRegistry()
        self.policy = PromotionPolicy()
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

        logger.info("Starting scheduled retrain")
        result = train()
        metrics = result.get("metrics", {})
        prod_brier = float(metrics.get("production_brier", 1.0))
        holdout_brier = float(metrics.get("holdout_brier", 1.0))

        record = self.registry.register_candidate(
            model_obj={"version": result["version"]},
            n_samples=int(metrics.get("samples", 0)),
            holdout_brier=holdout_brier,
            production_brier_at_train=prod_brier,
            holdout_auc=float(metrics.get("holdout_auc", 0.0)),
            positive_rate=float(metrics.get("positive_rate", 0.5)),
            brier_improvement=prod_brier - holdout_brier,
            model_id=f"lgbm_{result['model_id']}",
            lightgbm_txt_path=result["artifact_path"],
        )

        decision = self.policy.evaluate(
            model_id=record.model_id,
            n_samples=record.n_samples,
            holdout_brier=record.holdout_brier,
            production_brier=record.production_brier_at_train,
            holdout_auc=record.holdout_auc,
            positive_rate=record.positive_rate,
            walk_forward_brier=metrics.get("walk_forward_brier"),
        )

        gate_dicts = decision.to_dict()["gate_results"]
        promoted = False

        if decision.approved:
            promoted = self.registry.promote_to_production(
                record.model_id,
                decision.promoted_by,
                gate_results=gate_dicts,
            )
        elif decision.promoted_by == "pending_manual":
            self.registry.set_status(record.model_id, ModelStage.VALIDATED.value)
        else:
            self.registry.set_status(
                record.model_id,
                ModelStage.REJECTED.value,
                gate_results=gate_dicts,
            )

        record = self.registry.get_model(record.model_id) or record

        state = self._load_state()
        state["last_retrain"] = datetime.now(timezone.utc).isoformat()
        state["last_candidate_id"] = record.model_id
        state["last_promotion_decision"] = decision.to_dict()
        self._save_state(state)

        return {
            "status": "retrained",
            "model_id": record.model_id,
            "promotion": decision.to_dict(),
            "stage": record.status,
            "promoted": promoted,
            "model": record.to_dict(),
            "registry": self.registry.status_summary(),
        }

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

        out = self.registry.get_model(model_id).to_dict()
        out["promotion_decision"] = decision.to_dict()
        return out

    def rollback_model(self, model_id: str, rolled_back_by: str) -> dict:
        ok = self.registry.rollback(model_id, rolled_back_by)
        if not ok:
            raise ValueError(f"Rollback failed for {model_id}")
        rec = self.registry.get_model(model_id)
        return rec.to_dict() if rec else {}
