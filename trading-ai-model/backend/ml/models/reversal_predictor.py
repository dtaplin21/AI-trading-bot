"""
ml/models/reversal_predictor.py

Loads the trained LightGBM reversal probability model for a symbol
and returns P(reversal) for a given feature vector.

Was: hardcoded return 0.5
Now: real model inference using trained LightGBM + level history

Connects to:
  - train_reversal_models.py (produces the model this loads)
  - level_history.py (produces level features this needs)
  - cross_symbol_analysis.py (produces cx features this needs)
  - TradingPipelineSupervisor (calls this per bar)
  - ProbabilityGate (uses this output to decide trade entry)
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = os.getenv("MODEL_DIR", "models/reversal")


def _resolve_path(path: str | Path, base_dir: Path) -> Path:
    p = Path(path)
    if p.is_absolute() and p.exists():
        return p
    candidates = [
        p,
        base_dir / p.name,
        Path.cwd() / p,
        base_dir.parent / p,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return p


class ReversalPredictor:
    """
    Loads a trained LightGBM reversal probability model for one symbol
    and predicts P(reversal) given a feature dict or array.
    """

    def __init__(
        self,
        symbol: str,
        model_dir: Optional[str] = None,
    ):
        self.symbol = symbol.upper()
        self.model_dir = Path(model_dir or DEFAULT_MODEL_DIR) / self.symbol
        self.model = None
        self.meta: dict[str, Any] = {}
        self.feature_cols: list[str] = []
        self._loaded = False
        self._level_tracker = None

    def load(self) -> "ReversalPredictor":
        """Load the latest trained model for this symbol."""
        latest_path = self.model_dir / "latest.json"

        if not latest_path.exists():
            logger.warning(
                "%s: no trained model found at %s — using base rate fallback",
                self.symbol,
                latest_path,
            )
            return self

        try:
            self.meta = json.loads(latest_path.read_text())
            self.feature_cols = self.meta.get("feature_cols", [])

            model_path = _resolve_path(
                self.meta.get("model_path", ""),
                self.model_dir,
            )
            if not model_path.exists():
                logger.error("%s: model file missing: %s", self.symbol, model_path)
                return self

            with open(model_path, "rb") as f:
                self.model = pickle.load(f)

            levels_path = self.meta.get("levels_path")
            if levels_path:
                resolved_levels = _resolve_path(levels_path, self.model_dir)
                if resolved_levels.exists():
                    from ml.features.level_history import LevelHistoryTracker

                    asset_class = self.meta.get("asset_class", "equity")
                    if asset_class == "unknown":
                        from config.symbols import get_symbol_or_none

                        spec = get_symbol_or_none(self.symbol)
                        asset_class = spec.asset_class if spec else "equity"

                    self._level_tracker = LevelHistoryTracker(
                        self.symbol, asset_class
                    ).load(str(resolved_levels))

            self._loaded = True
            logger.info(
                "%s: loaded reversal model | AUC=%.4f | %d features | base_rate=%.1f%%",
                self.symbol,
                self.meta.get("metrics", {}).get("auc", 0),
                len(self.feature_cols),
                self.meta.get("metrics", {}).get("base_rate", 0) * 100,
            )
        except Exception as e:
            logger.error("%s: failed to load model: %s", self.symbol, e)

        return self

    def predict(self, features: dict) -> float:
        """
        Predict P(reversal) for a single candle.

        Returns float 0.0-1.0 — probability of reversal.
        Returns base_rate if model not loaded.
        """
        if not self._loaded or self.model is None:
            return self._base_rate_fallback()

        try:
            X = np.array(
                [float(features.get(col, 0.0)) for col in self.feature_cols]
            ).reshape(1, -1)

            prob = float(np.asarray(self.model.predict(X)).ravel()[0])
            return round(max(0.0, min(1.0, prob)), 4)

        except Exception as e:
            logger.warning("%s: predict error: %s", self.symbol, e)
            return self._base_rate_fallback()

    def predict_batch(self, features_df: pd.DataFrame) -> list[float]:
        """Predict P(reversal) for a DataFrame of candles."""
        if not self._loaded or self.model is None:
            return [self._base_rate_fallback()] * len(features_df)

        try:
            X = features_df.reindex(columns=self.feature_cols, fill_value=0.0)
            probs = np.asarray(self.model.predict(X.values)).ravel()
            return [round(float(p), 4) for p in probs]
        except Exception as e:
            logger.warning("%s: batch predict error: %s", self.symbol, e)
            return [self._base_rate_fallback()] * len(features_df)

    def _base_rate_fallback(self) -> float:
        """Return historical base rate if model unavailable."""
        return round(float(self.meta.get("metrics", {}).get("base_rate", 0.30)), 4)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def auc(self) -> float:
        return float(self.meta.get("metrics", {}).get("auc", 0.0))

    @property
    def base_rate(self) -> float:
        return self._base_rate_fallback()

    def __repr__(self) -> str:
        status = f"AUC={self.auc:.4f}" if self._loaded else "not loaded"
        return f"ReversalPredictor({self.symbol}, {status})"


_registry: dict[str, ReversalPredictor] = {}


def get_predictor(symbol: str, model_dir: Optional[str] = None) -> ReversalPredictor:
    """Get or create a ReversalPredictor for a symbol."""
    sym = symbol.upper()
    key = f"{sym}:{model_dir or DEFAULT_MODEL_DIR}"
    if key not in _registry:
        _registry[key] = ReversalPredictor(sym, model_dir).load()
    return _registry[key]


def build_prediction_features(
    symbol: str,
    ohlcv: pd.DataFrame | None,
    fused: Any = None,
    shared_features: dict | None = None,
) -> dict:
    """
    Assemble a feature dict for reversal inference from pipeline context.

    Merges shared features, fused outputs, technical features from OHLCV,
    and level features from the symbol's saved level tracker (if loaded).
    """
    features: dict[str, Any] = dict(shared_features or {})

    if fused is not None:
        if hasattr(fused, "model_dump"):
            features.update(fused.model_dump())
        elif isinstance(fused, dict):
            features.update(fused)

    if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 20:
        try:
            from ml.training.train_reversal_models import compute_technical_features

            tech = compute_technical_features(ohlcv)
            if not tech.empty:
                features.update(tech.iloc[-1].to_dict())
        except Exception as exc:
            logger.debug("%s: technical feature build failed: %s", symbol, exc)

    predictor = get_predictor(symbol)
    if predictor._level_tracker is not None and ohlcv is not None and not ohlcv.empty:
        try:
            close = float(ohlcv["close"].iloc[-1])
            features.update(predictor._level_tracker.get_features(close))
        except Exception as exc:
            logger.debug("%s: level feature build failed: %s", symbol, exc)

    return features


def predict_reversal(
    symbol: str,
    features: dict,
    ohlcv: pd.DataFrame | None = None,
    fused: Any = None,
    shared_features: dict | None = None,
) -> float:
    """
    Convenience function — predict P(reversal) for a symbol given features.

    If ohlcv/fused/shared_features are provided, builds a complete feature dict
    before inference.
    """
    if ohlcv is not None or fused is not None or shared_features:
        features = build_prediction_features(
            symbol,
            ohlcv=ohlcv,
            fused=fused,
            shared_features=shared_features or features,
        )
    return get_predictor(symbol).predict(features)


def reload_all(model_dir: Optional[str] = None) -> None:
    """Reload all cached predictors — call after retraining models."""
    _registry.clear()
    logger.info(
        "ReversalPredictor registry cleared — models will reload on next call"
    )
