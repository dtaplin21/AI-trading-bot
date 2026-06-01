"""
ml/promotion/promotion_policy.py

The single source of truth for model promotion decisions.

All gates are evaluated here — nothing is scattered.
Both RetrainPipeline and LearningAgent call this.

Auto-promote policy (stricter than today's candidate validation):

  Gate 1: Min labeled samples    >= 200
  Gate 2: Holdout Brier improve  >= 1% vs production (out-of-sample only)
  Gate 3: Holdout AUC/accuracy   >= 0.55 on validation split (NOT train)
  Gate 4: Max calibration drift  new_brier <= production_brier + 0.05
  Gate 5: Class balance          5% <= positive_rate <= 95%
  Gate 6: (optional) Walk-forward holdout must beat naive baseline

Auto-promote is controlled by:
  MODEL_AUTO_PROMOTE=true       enables auto-promotion
  MODEL_AUTO_PROMOTE=false      requires manual /models/{id}/promote call

Every promotion — auto or manual — is logged with full metrics JSON.
Last N production models kept for rollback.

Mark Douglas reminder:
  The model is retrained on outcomes — wins and losses equally.
  We do not promote a model because it looks good on recent data.
  We promote it because it improves on out-of-sample holdout data.
  Every prediction is still a guess. Better calibration = better guesses.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _ef(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _ei(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _eb(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


@dataclass
class PromotionGateResult:
    gate_name: str
    passed: bool
    value: float
    threshold: float
    reason: str

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{status} | {self.gate_name}: {self.value:.4f} "
            f"(threshold: {self.threshold:.4f}) — {self.reason}"
        )


@dataclass
class PromotionDecision:
    """Full result of one promotion evaluation."""

    model_id: str
    timestamp: datetime
    approved: bool
    promoted_by: str
    gate_results: list[PromotionGateResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    rollback_model: Optional[str] = None
    notes: str = ""

    def summary(self) -> str:
        lines = [
            f"Model promotion: {'APPROVED' if self.approved else 'REJECTED'} | {self.model_id}",
            f"By: {self.promoted_by} | {self.timestamp.isoformat()}",
        ]
        for g in self.gate_results:
            lines.append(f"  {g}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "timestamp": self.timestamp.isoformat(),
            "approved": self.approved,
            "promoted_by": self.promoted_by,
            "gate_results": [
                {
                    "gate": g.gate_name,
                    "passed": g.passed,
                    "value": g.value,
                    "threshold": g.threshold,
                    "reason": g.reason,
                }
                for g in self.gate_results
            ],
            "metrics": self.metrics,
            "rollback_model": self.rollback_model,
            "notes": self.notes,
        }


class PromotionPolicy:
    """
    Evaluates all promotion gates for a candidate model.
    Called by both RetrainPipeline and LearningAgent._try_retrain().
    """

    def __init__(self) -> None:
        self.auto_promote = _eb("MODEL_AUTO_PROMOTE", False)
        self.min_samples = _ei("MODEL_MIN_SAMPLES", 200)
        self.min_brier_improvement = _ef("MODEL_BRIER_IMPROVEMENT", 0.01)
        self.min_holdout_auc = _ef("MODEL_MIN_HOLDOUT_AUC", 0.55)
        self.max_calibration_drift = _ef("MODEL_MAX_DRIFT", 0.05)
        self.min_positive_rate = _ef("MODEL_MIN_POS_RATE", 0.05)
        self.max_positive_rate = _ef("MODEL_MAX_POS_RATE", 0.95)

        self._audit_path = Path(
            os.getenv("MODEL_AUDIT_LOG", "logs/model_promotions.jsonl")
        )
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "PromotionPolicy: auto_promote=%s | min_samples=%d | "
            "brier_improve=%.3f | min_auc=%.2f | max_drift=%.3f",
            self.auto_promote,
            self.min_samples,
            self.min_brier_improvement,
            self.min_holdout_auc,
            self.max_calibration_drift,
        )

    def evaluate(
        self,
        model_id: str,
        n_samples: int,
        holdout_brier: float,
        production_brier: float,
        holdout_auc: float,
        positive_rate: float,
        walk_forward_brier: Optional[float] = None,
        requested_by: str = "auto",
        extra_notes: str = "",
    ) -> PromotionDecision:
        gates: list[PromotionGateResult] = []

        gates.append(
            PromotionGateResult(
                gate_name="min_labeled_samples",
                passed=n_samples >= self.min_samples,
                value=float(n_samples),
                threshold=float(self.min_samples),
                reason=(
                    f"Trained on {n_samples} labeled outcomes"
                    if n_samples >= self.min_samples
                    else (
                        f"Only {n_samples} samples — "
                        "risk of overfitting to recent regime"
                    )
                ),
            )
        )

        brier_improvement = production_brier - holdout_brier
        gates.append(
            PromotionGateResult(
                gate_name="holdout_brier_improvement",
                passed=brier_improvement >= self.min_brier_improvement,
                value=brier_improvement,
                threshold=self.min_brier_improvement,
                reason=(
                    f"Brier improved {brier_improvement:.4f} "
                    f"(prod={production_brier:.4f} → new={holdout_brier:.4f})"
                    if brier_improvement >= self.min_brier_improvement
                    else (
                        f"Insufficient Brier improvement: {brier_improvement:.4f} "
                        f"< {self.min_brier_improvement:.4f}"
                    )
                ),
            )
        )

        gates.append(
            PromotionGateResult(
                gate_name="holdout_auc_accuracy",
                passed=holdout_auc >= self.min_holdout_auc,
                value=holdout_auc,
                threshold=self.min_holdout_auc,
                reason=(
                    f"Out-of-sample AUC={holdout_auc:.4f} passes minimum"
                    if holdout_auc >= self.min_holdout_auc
                    else (
                        f"Holdout AUC={holdout_auc:.4f} below {self.min_holdout_auc:.2f} "
                        "— model does not generalize"
                    )
                ),
            )
        )

        max_allowed_brier = production_brier + self.max_calibration_drift
        gates.append(
            PromotionGateResult(
                gate_name="max_calibration_drift",
                passed=holdout_brier <= max_allowed_brier,
                value=holdout_brier,
                threshold=max_allowed_brier,
                reason=(
                    f"No regression: new_brier={holdout_brier:.4f} "
                    f"<= max_allowed={max_allowed_brier:.4f}"
                    if holdout_brier <= max_allowed_brier
                    else (
                        f"Regression detected: new_brier={holdout_brier:.4f} "
                        f"> max_allowed={max_allowed_brier:.4f}"
                    )
                ),
            )
        )

        balance_ok = self.min_positive_rate <= positive_rate <= self.max_positive_rate
        gates.append(
            PromotionGateResult(
                gate_name="class_balance",
                passed=balance_ok,
                value=positive_rate,
                threshold=self.min_positive_rate,
                reason=(
                    f"Class balance OK: {positive_rate:.1%} positive labels"
                    if balance_ok
                    else (
                        f"Degenerate class balance: {positive_rate:.1%} — "
                        "model may predict only one class"
                    )
                ),
            )
        )

        if walk_forward_brier is not None:
            wf_improvement = production_brier - walk_forward_brier
            gates.append(
                PromotionGateResult(
                    gate_name="walk_forward_brier",
                    passed=wf_improvement >= 0,
                    value=walk_forward_brier,
                    threshold=production_brier,
                    reason=(
                        f"Walk-forward holds: wf_brier={walk_forward_brier:.4f} "
                        "better than prod"
                        if wf_improvement >= 0
                        else (
                            f"Walk-forward failed: wf_brier={walk_forward_brier:.4f} "
                            f"worse than prod={production_brier:.4f}"
                        )
                    ),
                )
            )

        all_passed = all(g.passed for g in gates)

        if all_passed and self.auto_promote:
            approved = True
            promoted_by = "auto_metrics"
        elif all_passed and not self.auto_promote:
            approved = False
            promoted_by = "pending_manual"
        else:
            approved = False
            promoted_by = "rejected"

        metrics = {
            "n_samples": n_samples,
            "holdout_brier": holdout_brier,
            "production_brier": production_brier,
            "brier_improvement": brier_improvement,
            "holdout_auc": holdout_auc,
            "positive_rate": positive_rate,
            "walk_forward_brier": walk_forward_brier,
            "auto_promote_flag": self.auto_promote,
            "requested_by": requested_by,
        }

        decision = PromotionDecision(
            model_id=model_id,
            timestamp=datetime.now(tz=timezone.utc),
            approved=approved,
            promoted_by=promoted_by,
            gate_results=gates,
            metrics=metrics,
            notes=extra_notes,
        )

        self._audit_log(decision)
        logger.info("\n%s", decision.summary())
        return decision

    def manual_approve(
        self, model_id: str, approved_by: str, notes: str = ""
    ) -> PromotionDecision:
        if not approved_by or approved_by == "auto":
            raise ValueError(
                "manual_approve requires a real approver name, not 'auto'"
            )

        decision = PromotionDecision(
            model_id=model_id,
            timestamp=datetime.now(tz=timezone.utc),
            approved=True,
            promoted_by=f"manual:{approved_by}",
            gate_results=[],
            metrics={},
            notes=f"Manual override by {approved_by}. {notes}",
        )
        self._audit_log(decision)
        logger.warning(
            "MANUAL PROMOTION: model=%s by=%s notes=%s",
            model_id,
            approved_by,
            notes,
        )
        return decision

    def _audit_log(self, decision: PromotionDecision) -> None:
        try:
            with open(self._audit_path, "a") as f:
                f.write(json.dumps(decision.to_dict()) + "\n")
        except Exception as e:
            logger.error("PromotionPolicy: audit log write failed: %s", e)

    def get_audit_history(self, last_n: int = 20) -> list[dict]:
        if not self._audit_path.exists():
            return []
        try:
            lines = self._audit_path.read_text().strip().split("\n")
            lines = [line for line in lines if line.strip()]
            records = [json.loads(line) for line in lines[-last_n:]]
            return list(reversed(records))
        except Exception as e:
            logger.error("PromotionPolicy: audit read failed: %s", e)
            return []
