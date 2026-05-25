"""Train LightGBM from pipeline observations."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from config.settings import get_settings
from ml.features.feature_vector import extract_vector
from ml.models.lightgbm_classifier import LightGBMSignalClassifier


def load_training_rows(log_dir: Path, store_rows: list[dict] | None = None) -> tuple[np.ndarray, np.ndarray]:
    rows: list[dict] = list(store_rows or [])
    obs_file = log_dir / "observations.jsonl"
    if obs_file.exists():
        for line in obs_file.read_text().strip().splitlines():
            if line:
                rows.append(json.loads(line))

    if not rows:
        raise ValueError("No training observations found")

    X, y = [], []
    for row in rows:
        features = row.get("features") or {}
        if not features:
            continue
        pred = row.get("prediction") or {}
        label = 1 if pred.get("should_start") else 0
        if row.get("executed") and row.get("risk_approved"):
            label = 1
        X.append(extract_vector(features))
        y.append(label)

    if len(X) < 10:
        raise ValueError(f"Insufficient samples for training: {len(X)}")

    return np.array(X), np.array(y)


def train(output_path: Path | None = None, version: str | None = None) -> dict:
    settings = get_settings()
    log_dir = Path("./logs/training")
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    store_rows = []
    try:
        from data.storage.timescale_store import TimescaleStore

        store = TimescaleStore()
        store_rows = store.load_observations(limit=50000)
    except Exception:
        pass

    X, y = load_training_rows(log_dir, store_rows)

    import lightgbm as lgb  # noqa: PLC0415

    train_data = lgb.Dataset(X, label=y)
    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "verbose": -1,
    }
    booster = lgb.train(params, train_data, num_boost_round=100)

    preds = booster.predict(X)
    auc = float(np.mean((preds > 0.5) == y))
    version = version or f"lgbm_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    model_id = str(uuid.uuid4())[:8]
    out = output_path or model_dir / f"candidate_{model_id}.txt"
    metrics = {"train_auc_proxy": auc, "samples": len(y), "positive_rate": float(y.mean())}
    LightGBMSignalClassifier.save_model(booster, out, version, metrics)

    return {
        "model_id": model_id,
        "version": version,
        "artifact_path": str(out),
        "metrics": metrics,
        "stage": "candidate",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LightGBM signal classifier")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()
    result = train(Path(args.output) if args.output else None)
    print(json.dumps(result, indent=2))
