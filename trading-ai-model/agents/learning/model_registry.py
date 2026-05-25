"""Model registry — candidate vs production with manual promotion."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from config.settings import get_settings


class ModelStage(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PAPER_TEST = "paper_test"
    APPROVED = "approved"
    PRODUCTION = "production"
    REJECTED = "rejected"


PROMOTION_FLOW = [
    ModelStage.CANDIDATE,
    ModelStage.VALIDATED,
    ModelStage.PAPER_TEST,
    ModelStage.APPROVED,
    ModelStage.PRODUCTION,
]


class ModelRegistry:
    """Tracks model artifacts; DB-backed when available, JSON fallback otherwise."""

    def __init__(self):
        settings = get_settings()
        self.model_dir = Path(settings.model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.model_dir / "registry.json"
        self.production_path = self.model_dir / "lightgbm_production.txt"
        self._store = None
        try:
            from data.storage.timescale_store import TimescaleStore

            self._store = TimescaleStore()
        except Exception:
            pass
        self._load_file_registry()

    def _load_file_registry(self) -> dict:
        if self.registry_file.exists():
            return json.loads(self.registry_file.read_text())
        return {"models": {}, "production_id": None}

    def _save_file_registry(self, data: dict) -> None:
        self.registry_file.write_text(json.dumps(data, indent=2, default=str))

    def register_candidate(self, model_id: str, version: str, artifact_path: str, metrics: dict) -> dict:
        entry = {
            "id": model_id,
            "version": version,
            "stage": ModelStage.CANDIDATE.value,
            "metrics": metrics,
            "artifact_path": artifact_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        reg = self._load_file_registry()
        reg["models"][model_id] = entry
        self._save_file_registry(reg)

        if self._store and self._store.available:
            self._upsert_db(entry)
        return entry

    def _upsert_db(self, entry: dict) -> None:
        sql = """
            INSERT INTO model_registry (id, version, stage, metrics, artifact_path)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (id) DO UPDATE SET
                stage = EXCLUDED.stage,
                metrics = EXCLUDED.metrics
        """
        with self._store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        entry["id"],
                        entry["version"],
                        entry["stage"],
                        json.dumps(entry.get("metrics", {})),
                        entry["artifact_path"],
                    ),
                )
            conn.commit()

    def list_models(self) -> list[dict]:
        reg = self._load_file_registry()
        return list(reg["models"].values())

    def get(self, model_id: str) -> Optional[dict]:
        return self._load_file_registry()["models"].get(model_id)

    def advance_stage(self, model_id: str, target: ModelStage) -> dict:
        reg = self._load_file_registry()
        entry = reg["models"].get(model_id)
        if not entry:
            raise KeyError(f"Model not found: {model_id}")
        current = ModelStage(entry["stage"])
        if target not in PROMOTION_FLOW:
            raise ValueError(f"Invalid stage: {target}")
        if PROMOTION_FLOW.index(target) <= PROMOTION_FLOW.index(current):
            raise ValueError(f"Cannot move from {current} to {target}")
        entry["stage"] = target.value
        if target == ModelStage.APPROVED:
            entry["approved_at"] = datetime.now(timezone.utc).isoformat()
        reg["models"][model_id] = entry
        self._save_file_registry(reg)
        if self._store and self._store.available:
            self._upsert_db(entry)
        return entry

    def promote_to_production(self, model_id: str, approved_by: str) -> dict:
        entry = self.get(model_id)
        if not entry:
            raise KeyError(f"Model not found: {model_id}")
        if entry["stage"] != ModelStage.APPROVED.value:
            raise ValueError("Model must be in APPROVED stage before production promotion")

        src = Path(entry["artifact_path"])
        if not src.exists():
            raise FileNotFoundError(f"Artifact missing: {src}")

        shutil.copy2(src, self.production_path)
        meta_src = src.with_suffix(".meta.json")
        if meta_src.exists():
            shutil.copy2(meta_src, self.production_path.with_suffix(".meta.json"))

        entry["stage"] = ModelStage.PRODUCTION.value
        entry["promoted_at"] = datetime.now(timezone.utc).isoformat()
        entry["approved_by"] = approved_by

        reg = self._load_file_registry()
        reg["models"][model_id] = entry
        reg["production_id"] = model_id
        self._save_file_registry(reg)
        return entry

    def production_model_id(self) -> Optional[str]:
        return self._load_file_registry().get("production_id")
