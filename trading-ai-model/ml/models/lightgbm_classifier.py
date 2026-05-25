"""LightGBM signal classifier with model persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from ml.features.feature_vector import FEATURE_KEYS, extract_vector
from ml.models.base_model import BaseModel

logger = logging.getLogger(__name__)


class LightGBMSignalClassifier(BaseModel):
    """Loads production LightGBM model; falls back to rules if missing."""

    def __init__(self, model_path: Optional[str] = None):
        settings = get_settings()
        self.model_dir = Path(settings.model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = Path(model_path) if model_path else self.model_dir / "lightgbm_production.txt"
        self.meta_path = self.model_path.with_suffix(".meta.json")
        self._model = None
        self._version = "rule_fallback"
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            return
        try:
            import lightgbm as lgb  # noqa: PLC0415

            self._model = lgb.Booster(model_file=str(self.model_path))
            if self.meta_path.exists():
                meta = json.loads(self.meta_path.read_text())
                self._version = meta.get("version", "lightgbm")
            else:
                self._version = "lightgbm"
        except Exception as exc:
            logger.warning("LightGBM load failed: %s", exc)
            self._model = None

    @property
    def version(self) -> str:
        return self._version

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def predict(self, features: dict) -> dict:
        if self._model is None:
            return self._rule_fallback(features)

        try:
            import numpy as np  # noqa: PLC0415

            vec = np.array([extract_vector(features)])
            prob = float(self._model.predict(vec)[0])
            if prob > 1 or prob < 0:
                prob = 1 / (1 + np.exp(-prob))
            return {
                "signal_probability": prob,
                "model_version": self._version,
                "model_type": "lightgbm",
            }
        except Exception as exc:
            logger.warning("LightGBM predict failed: %s", exc)
            return self._rule_fallback(features)

    def _rule_fallback(self, features: dict) -> dict:
        rank = float(features.get("signal_rank", 50)) / 100
        ev = min(1.0, max(0.0, float(features.get("strategy_ev", 0)) / 20))
        cont = float(features.get("markov_continuation_probability", 0.5))
        prob = (rank + ev + cont) / 3
        return {
            "signal_probability": prob,
            "model_version": "rule_fallback",
            "model_type": "rules",
        }

    @staticmethod
    def save_model(booster, path: Path, version: str, metrics: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        booster.save_model(str(path))
        meta = {"version": version, "metrics": metrics, "features": FEATURE_KEYS}
        path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
