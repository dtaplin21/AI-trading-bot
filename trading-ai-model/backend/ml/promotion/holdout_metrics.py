"""Holdout metrics for PromotionPolicy — train split never used for gates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

HOLDOUT_FRACTION = float(__import__("os").getenv("MODEL_HOLDOUT_FRACTION", "0.2"))


def holdout_auc(preds: np.ndarray, y: np.ndarray) -> float:
    """Out-of-sample AUC or accuracy proxy when AUC undefined."""
    if len(y) == 0:
        return 0.0
    if len(np.unique(y)) < 2:
        return float(np.mean((preds > 0.5) == y))
    try:
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y, preds))
    except Exception:
        return float(np.mean((preds > 0.5) == y))


def holdout_brier(preds: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((preds - y) ** 2))


def production_brier_on_holdout(
    X_val: np.ndarray,
    y_val: np.ndarray,
    production_path: Path | None = None,
    fallback: float = 1.0,
) -> float:
    """Score current production artifact on the same holdout rows."""
    from config.settings import get_settings
    from ml.models.lightgbm_classifier import LightGBMSignalClassifier

    path = production_path or Path(get_settings().model_dir) / "lightgbm_production.txt"
    if not path.exists():
        return fallback

    try:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(path))
        preds = np.array(booster.predict(X_val))
        return holdout_brier(preds, y_val)
    except Exception as exc:
        logger.warning("production_brier_on_holdout failed: %s", exc)
        return fallback


def metrics_from_booster(
    booster: Any,
    X: np.ndarray,
    y: np.ndarray,
    *,
    holdout_fraction: float = HOLDOUT_FRACTION,
    production_path: Path | None = None,
) -> dict[str, float]:
    """
    Evaluate booster on trailing holdout split (time-ordered rows assumed).
    """
    n = len(y)
    if n < 10:
        raise ValueError(f"Insufficient samples for holdout metrics: {n}")

    split = max(int(n * (1.0 - holdout_fraction)), n - max(n // 5, 10))
    split = min(split, n - 5)
    X_val = X[split:]
    y_val = y[split:]

    preds = np.array(booster.predict(X_val))
    prod_brier = production_brier_on_holdout(X_val, y_val, production_path)

    return {
        "samples": float(n),
        "holdout_brier": holdout_brier(preds, y_val),
        "holdout_auc": holdout_auc(preds, y_val),
        "positive_rate": float(np.mean(y)),
        "production_brier": prod_brier,
        "train_auc_proxy": float(np.mean((booster.predict(X[:split]) > 0.5) == y[:split])),
    }
