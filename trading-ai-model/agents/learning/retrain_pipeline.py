"""Safe retraining pipeline — never promotes live without manual approval."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from agents.learning.model_registry import ModelRegistry, ModelStage
from config.settings import get_settings
from ml.training.train_lightgbm import train

logger = logging.getLogger(__name__)


class RetrainPipeline:
    """
    Safe learning loop:
    Observe → Store → Label → Backtest → Retrain → Validate → Paper test → Approve → Deploy
    """

    def __init__(self):
        self.settings = get_settings()
        self.registry = ModelRegistry()
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
        return datetime.now(timezone.utc) - last_dt >= timedelta(days=self.settings.retrain_schedule_days)

    def run_scheduled_retrain(self, force: bool = False) -> dict:
        if not force and not self.due_for_retrain():
            return {"status": "skipped", "reason": "not_due", "next_in_days": self.settings.retrain_schedule_days}

        logger.info("Starting scheduled retrain")
        result = train()
        entry = self.registry.register_candidate(
            model_id=result["model_id"],
            version=result["version"],
            artifact_path=result["artifact_path"],
            metrics=result["metrics"],
        )

        validation = self._validate_candidate(result)
        if validation["passed"]:
            self.registry.advance_stage(result["model_id"], ModelStage.VALIDATED)

        state = self._load_state()
        state["last_retrain"] = datetime.now(timezone.utc).isoformat()
        state["last_candidate_id"] = result["model_id"]
        self._save_state(state)

        return {
            "status": "retrained",
            "model_id": result["model_id"],
            "validation": validation,
            "stage": ModelStage.VALIDATED.value if validation["passed"] else ModelStage.CANDIDATE.value,
        }

    def _validate_candidate(self, train_result: dict) -> dict:
        metrics = train_result.get("metrics", {})
        samples = metrics.get("samples", 0)
        auc = metrics.get("train_auc_proxy", 0)
        passed = samples >= 50 and auc >= 0.55
        return {"passed": passed, "samples": samples, "auc_proxy": auc}

    def approve_for_paper_test(self, model_id: str) -> dict:
        self.registry.advance_stage(model_id, ModelStage.PAPER_TEST)
        return self.registry.get(model_id)

    def approve_model(self, model_id: str) -> dict:
        self.registry.advance_stage(model_id, ModelStage.APPROVED)
        return self.registry.get(model_id)

    def promote_model(self, model_id: str, approved_by: str) -> dict:
        return self.registry.promote_to_production(model_id, approved_by)
