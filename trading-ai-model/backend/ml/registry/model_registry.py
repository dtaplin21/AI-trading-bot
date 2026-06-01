"""
ml/registry/model_registry.py

Tracks every trained model, its metrics, and its status.
Manages the production pointer and rollback capability.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

def _production_path() -> Path:
    return Path(os.getenv("MODEL_PRODUCTION_PATH", "ml/models/lightgbm_production.pkl"))


def _archive_dir() -> Path:
    return Path(os.getenv("MODEL_ARCHIVE_DIR", "ml/models/archive"))


def _registry_file() -> Path:
    return Path(os.getenv("MODEL_REGISTRY_FILE", "ml/models/registry.json"))


def _rollback_keep() -> int:
    return int(os.getenv("MODEL_N_ROLLBACK_KEEP", os.getenv("MODEL_ROLLBACK_KEEP", "5")))


@dataclass
class ModelRecord:
    """One entry in the model registry."""

    model_id: str
    created_at: str
    status: str
    model_path: str
    n_samples: int = 0
    holdout_brier: float = 1.0
    production_brier_at_train: float = 1.0
    holdout_auc: float = 0.0
    positive_rate: float = 0.0
    brier_improvement: float = 0.0
    promoted_by: str = ""
    promoted_at: Optional[str] = None
    superseded_at: Optional[str] = None
    notes: str = ""
    gate_results: list = field(default_factory=list)
    lightgbm_txt_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> ModelRecord:
        valid = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in valid})


class ModelRegistry:
    """
    Single source of truth for all model versions.
    """

    def __init__(self) -> None:
        self.archive_dir = _archive_dir()
        self.production_path = _production_path()
        self.registry_file = _registry_file()
        self.rollback_keep = _rollback_keep()
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.production_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, ModelRecord] = {}
        self._load_registry()
        logger.info(
            "ModelRegistry: %d models | production=%s",
            len(self._records),
            self._get_production_id() or "none",
        )

    def register_candidate(
        self,
        model_obj: Any,
        n_samples: int,
        holdout_brier: float,
        production_brier_at_train: float,
        holdout_auc: float,
        positive_rate: float,
        brier_improvement: float,
        notes: str = "",
        *,
        model_id: Optional[str] = None,
        lightgbm_txt_path: Optional[str] = None,
    ) -> ModelRecord:
        """Save a newly trained model and create its registry entry."""
        model_id = model_id or (
            f"lgbm_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        archive_path = str(self.archive_dir / f"{model_id}.pkl")

        payload = model_obj
        if lightgbm_txt_path:
            payload = {
                "type": "lightgbm",
                "model": model_obj,
                "lightgbm_txt_path": lightgbm_txt_path,
            }

        self._save_pkl(payload, archive_path)

        txt_archive: Optional[str] = None
        if lightgbm_txt_path and Path(lightgbm_txt_path).exists():
            txt_archive = str(self.archive_dir / f"{model_id}.txt")
            shutil.copy2(lightgbm_txt_path, txt_archive)
            meta = Path(lightgbm_txt_path).with_suffix(".meta.json")
            if meta.exists():
                shutil.copy2(meta, Path(txt_archive).with_suffix(".meta.json"))

        record = ModelRecord(
            model_id=model_id,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            status="candidate",
            model_path=archive_path,
            n_samples=n_samples,
            holdout_brier=holdout_brier,
            production_brier_at_train=production_brier_at_train,
            holdout_auc=holdout_auc,
            positive_rate=positive_rate,
            brier_improvement=brier_improvement,
            notes=notes,
            lightgbm_txt_path=txt_archive,
        )
        self._records[model_id] = record
        self._save_registry()

        logger.info(
            "ModelRegistry: registered candidate %s | n=%d brier=%.4f "
            "improvement=%.4f auc=%.3f",
            model_id,
            n_samples,
            holdout_brier,
            brier_improvement,
            holdout_auc,
        )
        return record

    def set_status(
        self,
        model_id: str,
        status: str,
        notes: str = "",
        gate_results: list | None = None,
    ) -> Optional[ModelRecord]:
        record = self._records.get(model_id)
        if not record:
            return None
        record.status = status
        if notes:
            record.notes = (record.notes + " | " + notes).strip(" |")
        if gate_results is not None:
            record.gate_results = gate_results
        self._save_registry()
        return record

    def promote_to_production(
        self,
        model_id: str,
        promoted_by: str,
        gate_results: list | None = None,
        notes: str = "",
    ) -> bool:
        record = self._records.get(model_id)
        if not record:
            logger.error("ModelRegistry: promote failed — model_id=%s not found", model_id)
            return False

        old_id = self._get_production_id()
        if old_id and old_id in self._records:
            self._records[old_id].status = "superseded"
            self._records[old_id].superseded_at = datetime.now(tz=timezone.utc).isoformat()
            logger.info("ModelRegistry: superseded previous production %s", old_id)

        record.status = "production"
        record.promoted_by = promoted_by
        record.promoted_at = datetime.now(tz=timezone.utc).isoformat()
        record.gate_results = gate_results or record.gate_results
        if notes:
            record.notes = (record.notes + " | " + notes).strip(" |")

        try:
            shutil.copy2(record.model_path, self.production_path)
            self._sync_lightgbm_production_txt(record)
            logger.info(
                "ModelRegistry: PROMOTED %s → %s by=%s",
                model_id,
                self.production_path,
                promoted_by,
            )
        except Exception as e:
            logger.error("ModelRegistry: copy to production failed: %s", e)
            return False

        self._prune_old_models()
        self._save_registry()
        self._notify_promotion(model_id, promoted_by, record)
        return True

    def rollback(self, target_model_id: str, rolled_back_by: str) -> bool:
        target = self._records.get(target_model_id)
        if not target:
            logger.error("ModelRegistry: rollback target %s not found", target_model_id)
            return False
        if not Path(target.model_path).exists():
            logger.error("ModelRegistry: rollback model file missing: %s", target.model_path)
            return False

        old_id = self._get_production_id()
        if old_id and old_id in self._records:
            self._records[old_id].status = "rolled_back"
            self._records[old_id].notes += f" | rolled_back by {rolled_back_by}"

        return self.promote_to_production(
            target_model_id,
            promoted_by=f"rollback:{rolled_back_by}",
            notes=f"Rollback from {old_id}",
        )

    def load_production_model(self) -> Optional[Any]:
        if not self.production_path.exists():
            logger.warning(
                "ModelRegistry: no production model at %s", self.production_path
            )
            return None
        try:
            with open(self.production_path, "rb") as f:
                model = pickle.load(f)
            logger.debug(
                "ModelRegistry: loaded production model from %s", self.production_path
            )
            if isinstance(model, dict) and "model" in model:
                return model["model"]
            return model
        except Exception as e:
            logger.error("ModelRegistry: load production model failed: %s", e)
            return None

    def get_production_record(self) -> Optional[ModelRecord]:
        pid = self._get_production_id()
        return self._records.get(pid) if pid else None

    def get_production_brier(self) -> float:
        rec = self.get_production_record()
        return rec.holdout_brier if rec else 1.0

    def production_model_id(self) -> Optional[str]:
        return self._get_production_id()

    def list_models(self, status: Optional[str] = None) -> list[ModelRecord]:
        records = list(self._records.values())
        if status:
            records = [r for r in records if r.status == status]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def list_models_dict(self, status: Optional[str] = None) -> list[dict]:
        return [r.to_dict() for r in self.list_models(status=status)]

    def get_model(self, model_id: str) -> Optional[ModelRecord]:
        return self._records.get(model_id)

    def status_summary(self) -> dict:
        from collections import Counter

        counts = Counter(r.status for r in self._records.values())
        prod = self.get_production_record()
        return {
            "total_models": len(self._records),
            "by_status": dict(counts),
            "production_id": prod.model_id if prod else None,
            "production_brier": prod.holdout_brier if prod else None,
            "production_by": prod.promoted_by if prod else None,
            "production_at": prod.promoted_at if prod else None,
        }

    def _sync_lightgbm_production_txt(self, record: ModelRecord) -> None:
        """Keep LightGBMSignalClassifier .txt path in sync with production pkl."""
        settings = get_settings()
        txt_dest = Path(settings.model_dir) / "lightgbm_production.txt"
        txt_dest.parent.mkdir(parents=True, exist_ok=True)

        src = record.lightgbm_txt_path
        if not src or not Path(src).exists():
            try:
                with open(record.model_path, "rb") as f:
                    payload = pickle.load(f)
                if isinstance(payload, dict):
                    src = payload.get("lightgbm_txt_path")
            except Exception:
                src = None

        if src and Path(src).exists():
            shutil.copy2(src, txt_dest)
            meta = Path(src).with_suffix(".meta.json")
            if meta.exists():
                shutil.copy2(meta, txt_dest.with_suffix(".meta.json"))

    def _get_production_id(self) -> Optional[str]:
        for rid, rec in self._records.items():
            if rec.status == "production":
                return rid
        return None

    def _prune_old_models(self) -> None:
        superseded = [r for r in self._records.values() if r.status == "superseded"]
        superseded.sort(key=lambda r: r.superseded_at or "", reverse=True)
        for rec in superseded[self.rollback_keep :]:
            try:
                Path(rec.model_path).unlink(missing_ok=True)
                if rec.lightgbm_txt_path:
                    Path(rec.lightgbm_txt_path).unlink(missing_ok=True)
                    Path(rec.lightgbm_txt_path).with_suffix(".meta.json").unlink(
                        missing_ok=True
                    )
                logger.debug("ModelRegistry: pruned old model %s", rec.model_id)
            except Exception:
                pass
            del self._records[rec.model_id]

    def _save_pkl(self, model_obj: Any, path: str) -> None:
        try:
            with open(path, "wb") as f:
                pickle.dump(model_obj, f)
        except Exception as e:
            logger.error("ModelRegistry: save pkl failed: %s", e)
            raise

    def _load_registry(self) -> None:
        if not self.registry_file.exists():
            return
        try:
            data = json.loads(self.registry_file.read_text())
            for model_id, d in data.items():
                self._records[model_id] = ModelRecord.from_dict(d)
        except Exception as e:
            logger.error("ModelRegistry: load registry failed: %s", e)

    def _save_registry(self) -> None:
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            data = {mid: rec.to_dict() for mid, rec in self._records.items()}
            self.registry_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error("ModelRegistry: save registry failed: %s", e)

    def _notify_promotion(self, model_id: str, promoted_by: str, record: ModelRecord) -> None:
        msg = (
            f"Model promoted to production\n"
            f"  ID: {model_id}\n"
            f"  By: {promoted_by}\n"
            f"  Brier: {record.holdout_brier:.4f} "
            f"(improvement: {record.brier_improvement:+.4f})\n"
            f"  AUC: {record.holdout_auc:.3f}\n"
            f"  Samples: {record.n_samples}\n"
            f"  Time: {record.promoted_at}"
        )
        logger.info("MODEL PROMOTION NOTIFICATION:\n%s", msg)

        slack_url = os.getenv("NOTIFY_SLACK_WEBHOOK", "")
        if slack_url:
            try:
                import urllib.request

                payload = json.dumps({"text": msg}).encode()
                req = urllib.request.Request(
                    slack_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                logger.warning("ModelRegistry: Slack notify failed: %s", e)
