"""
ml/training/retrain_pipeline.py

Unified retrain pipeline — WorldState rows → walk-forward train → PromotionPolicy → registry.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

from ml.promotion.holdout_metrics import holdout_auc
from ml.promotion.promotion_policy import PromotionDecision, PromotionPolicy
from ml.registry.model_registry import ModelRegistry
from pipeline.world_state_store import WorldStateStore

logger = logging.getLogger(__name__)

LGB_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": float(os.getenv("LIGHTGBM_LEARNING_RATE", "0.05")),
    "num_leaves": int(os.getenv("LIGHTGBM_NUM_LEAVES", "31")),
    "min_data_in_leaf": int(os.getenv("LIGHTGBM_MIN_LEAF", "20")),
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
}
N_ESTIMATORS = int(os.getenv("LIGHTGBM_N_ESTIMATORS", "200"))
TRAIN_SPLIT = float(os.getenv("LIGHTGBM_TRAIN_SPLIT", "0.80"))
MIN_HOLDOUT_ROWS = int(os.getenv("MODEL_MIN_HOLDOUT_ROWS", "20"))

META_COLS = frozenset(
    {
        "label",
        "_symbol",
        "_timeframe",
        "_regime",
        "_timestamp",
        "actual_pnl",
        "actual_r",
        "hit_target",
        "hit_stop",
        "snapshot_id",
        "trade_id",
    }
)

_prediction_reload_callbacks: list[Callable[[], None]] = []


def register_prediction_reload(callback: Callable[[], None]) -> None:
    _prediction_reload_callbacks.append(callback)


class RetrainResult:
    """Full output of one retrain cycle."""

    def __init__(self) -> None:
        self.model_id: Optional[str] = None
        self.n_train: int = 0
        self.n_holdout: int = 0
        self.holdout_brier: float = 1.0
        self.production_brier: float = 1.0
        self.holdout_auc: float = 0.0
        self.positive_rate: float = 0.0
        self.brier_improvement: float = 0.0
        self.promoted: bool = False
        self.promoted_by: str = ""
        self.decision: Optional[PromotionDecision] = None
        self.error: Optional[str] = None
        self.skipped: bool = False
        self.skip_reason: str = ""
        self.stage: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"Retrain SKIPPED: {self.skip_reason}"
        if self.error:
            return f"Retrain ERROR: {self.error}"
        return (
            f"Retrain: n_train={self.n_train} n_holdout={self.n_holdout} | "
            f"holdout_brier={self.holdout_brier:.4f} "
            f"prod_brier={self.production_brier:.4f} "
            f"improvement={self.brier_improvement:+.4f} | "
            f"promoted={'YES by ' + self.promoted_by if self.promoted else 'NO'}"
        )

    def to_dict(self) -> dict:
        out = {
            "model_id": self.model_id,
            "n_train": self.n_train,
            "n_holdout": self.n_holdout,
            "holdout_brier": self.holdout_brier,
            "production_brier": self.production_brier,
            "holdout_auc": self.holdout_auc,
            "positive_rate": self.positive_rate,
            "brier_improvement": self.brier_improvement,
            "promoted": self.promoted,
            "promoted_by": self.promoted_by,
            "error": self.error,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "stage": self.stage,
            "summary": self.summary(),
        }
        if self.decision:
            out["promotion"] = self.decision.to_dict()
        return out


class RetrainPipeline:
    """End-to-end retrain: train fold → holdout eval → gates → optional promote."""

    def __init__(
        self,
        world_store: WorldStateStore,
        model_registry: ModelRegistry,
        promotion_policy: PromotionPolicy,
    ) -> None:
        self._world = world_store
        self._registry = model_registry
        self._policy = promotion_policy
        self._last_run: Optional[datetime] = None

    def run(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        force: bool = False,
        requested_by: str = "auto",
    ) -> RetrainResult:
        result = RetrainResult()

        try:
            min_samples = int(os.getenv("MODEL_MIN_SAMPLES", "200"))
            rows = self._world.get_training_rows(
                symbol=symbol,
                timeframe=timeframe,
                last_n_days=90,
            )

            if len(rows) < min_samples and not force:
                result.skipped = True
                result.skip_reason = (
                    f"Only {len(rows)} labeled rows, need {min_samples}. "
                    "Use force=True to override."
                )
                logger.info("RetrainPipeline: skipped — %s", result.skip_reason)
                return result

            logger.info("RetrainPipeline: starting with %d rows", len(rows))

            rows.sort(key=lambda r: r.get("_timestamp", ""))
            split_idx = int(len(rows) * TRAIN_SPLIT)
            split_idx = max(split_idx, min_samples)
            split_idx = min(split_idx, len(rows) - MIN_HOLDOUT_ROWS)
            train_rows = rows[:split_idx]
            holdout_rows = rows[split_idx:]

            if len(holdout_rows) < MIN_HOLDOUT_ROWS:
                result.skipped = True
                result.skip_reason = f"Holdout set too small: {len(holdout_rows)} rows"
                return result

            import numpy as np

            feature_cols = self._get_feature_cols(rows[0])
            X_train = np.array(
                [[r.get(c, 0.0) or 0.0 for c in feature_cols] for r in train_rows]
            )
            y_train = np.array([r["label"] for r in train_rows])
            X_hold = np.array(
                [[r.get(c, 0.0) or 0.0 for c in feature_cols] for r in holdout_rows]
            )
            y_hold = np.array([r["label"] for r in holdout_rows])

            result.n_train = len(X_train)
            result.n_holdout = len(X_hold)
            result.positive_rate = float(np.mean([r["label"] for r in rows]))

            model = self._train(X_train, y_train, X_hold, y_hold)
            if model is None:
                result.error = "LightGBM training failed"
                return result

            preds_hold = np.array(model.predict(X_hold))
            result.holdout_brier = float(np.mean((preds_hold - y_hold) ** 2))
            result.holdout_auc = holdout_auc(preds_hold, y_hold)

            result.production_brier = self._registry.get_production_brier()
            result.brier_improvement = result.production_brier - result.holdout_brier

            candidate = self._registry.register_candidate(
                model_obj={"model": model, "feature_cols": feature_cols},
                n_samples=len(rows),
                holdout_brier=result.holdout_brier,
                production_brier_at_train=result.production_brier,
                holdout_auc=result.holdout_auc,
                positive_rate=result.positive_rate,
                brier_improvement=result.brier_improvement,
                notes=f"requested_by={requested_by}",
            )
            result.model_id = candidate.model_id
            result.stage = candidate.status

            decision = self._policy.evaluate(
                model_id=candidate.model_id,
                n_samples=len(rows),
                holdout_brier=result.holdout_brier,
                production_brier=result.production_brier,
                holdout_auc=result.holdout_auc,
                positive_rate=result.positive_rate,
                requested_by=requested_by,
            )
            result.decision = decision
            gate_dicts = decision.to_dict()["gate_results"]

            if decision.approved:
                promoted = self._registry.promote_to_production(
                    model_id=candidate.model_id,
                    promoted_by=decision.promoted_by,
                    gate_results=gate_dicts,
                    notes=decision.notes,
                )
                result.promoted = promoted
                result.promoted_by = decision.promoted_by
                if promoted:
                    self._reload_prediction_models()
                    result.stage = "production"
                    logger.info(
                        "RetrainPipeline: PROMOTED %s | brier improvement %+.4f",
                        candidate.model_id,
                        result.brier_improvement,
                    )
            elif decision.promoted_by == "pending_manual":
                self._registry.set_status(
                    candidate.model_id,
                    "validated",
                    gate_results=gate_dicts,
                )
                result.stage = "validated"
            else:
                self._registry.set_status(
                    candidate.model_id,
                    "rejected",
                    gate_results=gate_dicts,
                )
                result.stage = "rejected"
                failed = [g.reason for g in decision.gate_results if not g.passed]
                logger.info(
                    "RetrainPipeline: NOT promoted — %s",
                    "; ".join(failed) if failed else decision.promoted_by,
                )

        except Exception as e:
            result.error = str(e)
            logger.error("RetrainPipeline: unexpected error: %s", e, exc_info=True)

        self._last_run = datetime.now(tz=timezone.utc)
        logger.info("RetrainPipeline: %s", result.summary())
        return result

    def _train(self, X_train, y_train, X_val, y_val) -> Optional[Any]:
        try:
            import lightgbm as lgb

            train_data = lgb.Dataset(X_train, label=y_train)
            val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
            return lgb.train(
                LGB_PARAMS,
                train_data,
                num_boost_round=N_ESTIMATORS,
                valid_sets=[val_data],
                callbacks=[
                    lgb.early_stopping(30, verbose=False),
                    lgb.log_evaluation(50),
                ],
            )
        except ImportError:
            logger.warning("RetrainPipeline: LightGBM not installed")
            return None
        except Exception as e:
            logger.error("RetrainPipeline: training error: %s", e)
            return None

    def _get_feature_cols(self, sample_row: dict) -> list[str]:
        return [k for k in sample_row.keys() if k not in META_COLS]

    def _reload_prediction_models(self) -> None:
        """Hot-reload classifiers after production pointer swap."""
        os.environ["LIGHTGBM_MODEL_PATH"] = str(self._registry.production_path)
        try:
            from ml.models.lightgbm_classifier import LightGBMSignalClassifier

            LightGBMSignalClassifier.reload_singleton()
        except Exception as e:
            logger.debug("RetrainPipeline: classifier reload: %s", e)

        for callback in _prediction_reload_callbacks:
            try:
                callback()
            except Exception as e:
                logger.warning("RetrainPipeline: reload callback failed: %s", e)

        logger.info(
            "RetrainPipeline: production model at %s",
            self._registry.production_path,
        )

    @property
    def last_run(self) -> Optional[datetime]:
        return self._last_run

    def due_for_retrain(self, schedule_days: int) -> bool:
        if self._last_run is None:
            return True
        return datetime.now(tz=timezone.utc) - self._last_run >= timedelta(
            days=schedule_days
        )
