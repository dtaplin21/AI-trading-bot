"""
ml/features/feature_pipeline.py

Orchestrates all feature computation for a single candle or DataFrame.
Replaces the passthrough stub that did nothing.

Was: return layer_output (passthrough)
Now: runs all feature extractors and returns complete feature dict

Connects to:
  - ReversalPredictor.predict() — needs features in the right format
  - TradingPipelineSupervisor — calls this per bar before prediction
  - train_reversal_models.py — uses same pipeline for training consistency

This is the single place that defines "what features go into the model."
Training and inference use identical feature computation so there's
no train/serve skew.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from data.storage.feature_store import get_feature_store
from ml.features import (
    candlestick_features,
    elliott_features,
    fibonacci_features,
    fractal_features,
    gann_features,
    harmonic_features,
    markov_features,
    number_theory_features,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = os.getenv("MODEL_DIR", "models/reversal")

TIER4_EXTRACTORS = (
    candlestick_features.extract,
    fibonacci_features.extract,
    fractal_features.extract,
    gann_features.extract,
    harmonic_features.extract,
    elliott_features.extract,
    markov_features.extract,
    number_theory_features.extract,
)

_analyzer_cache: dict[str, Any] = {}
_significance_cache: dict[str, Any] = {}


def _load_significance_analyzer(symbol: str, model_dir: str | Path | None = None):
    """Load fitted LevelSignificanceAnalyzer for a symbol."""
    sym = symbol.upper()
    base = Path(model_dir or DEFAULT_MODEL_DIR)
    cache_key = f"{base.resolve()}:{sym}"
    if cache_key in _significance_cache:
        return _significance_cache[cache_key]

    profile_path = base / sym / "significance_latest.json"
    if not profile_path.exists():
        _significance_cache[cache_key] = None
        return None

    try:
        from ml.features.level_significance import LevelSignificanceAnalyzer

        analyzer = LevelSignificanceAnalyzer(sym, "equity").load(str(profile_path))
        _significance_cache[cache_key] = analyzer
        return analyzer
    except Exception as exc:
        logger.warning(
            "Failed to load significance profile for %s from %s: %s",
            sym,
            profile_path,
            exc,
        )
        _significance_cache[cache_key] = None
        return None


def _load_cross_symbol_analyzer(model_dir: str | Path | None = None):
    """Load fitted CrossSymbolAnalyzer from the shared training profile."""
    base = Path(model_dir or DEFAULT_MODEL_DIR)
    cache_key = str(base.resolve())
    if cache_key in _analyzer_cache:
        return _analyzer_cache[cache_key]

    profile_path = base / "cross_symbol_profile.json"
    if not profile_path.exists():
        _analyzer_cache[cache_key] = None
        return None

    try:
        from ml.features.cross_symbol_analysis import CrossSymbolAnalyzer

        analyzer = CrossSymbolAnalyzer().load(str(profile_path))
        _analyzer_cache[cache_key] = analyzer
        return analyzer
    except Exception as exc:
        logger.warning("Failed to load cross-symbol profile from %s: %s", profile_path, exc)
        _analyzer_cache[cache_key] = None
        return None


class FeaturePipeline:
    """
    Runs all feature extractors and returns a complete feature dict
    ready for model inference.

    The pipeline mirrors what train_reversal_models.py computes
    during training — same features, same order.

    Usage:
        pipeline = FeaturePipeline.for_symbol("EURUSD")
        features = pipeline.compute(df_5m)
        prob = reversal_predictor.predict(features)
    """

    def __init__(
        self,
        symbol: str = "",
        tracker=None,
        analyzer=None,
        significance=None,
        all_trackers: Optional[dict] = None,
        model_dir: str | Path | None = None,
    ):
        self.symbol = symbol.upper() if symbol else ""
        self.tracker = tracker
        self.analyzer = analyzer
        self.significance = significance
        self.all_trackers = all_trackers or {}
        self.model_dir = Path(model_dir or DEFAULT_MODEL_DIR)

    @classmethod
    def for_symbol(cls, symbol: str, model_dir: str | Path | None = None) -> "FeaturePipeline":
        """Build a pipeline with level tracker and cross-symbol profile for inference."""
        from ml.models.reversal_predictor import get_predictor

        sym = symbol.upper()
        base = Path(model_dir or DEFAULT_MODEL_DIR)
        predictor = get_predictor(sym, str(base))
        return cls(
            symbol=sym,
            tracker=predictor._level_tracker,
            analyzer=_load_cross_symbol_analyzer(base),
            significance=_load_significance_analyzer(sym, base),
            all_trackers={},
            model_dir=base,
        )

    def compute(self, df: pd.DataFrame) -> dict:
        """
        Compute all features for the most recent candle.

        Args:
            df: 5m OHLCV DataFrame. Must have enough history for indicators
                (at least 100 bars recommended).

        Returns:
            dict of feature_name → float value
        """
        if df.empty or len(df) < 20:
            return {}

        features: dict[str, Any] = {}
        features.update(self._technical_features(df))

        if self.tracker is not None:
            current_price = float(df["close"].iloc[-1])
            features.update(self.tracker.get_features(current_price))

        if self.significance is not None:
            current_price = float(df["close"].iloc[-1])
            features.update(self.significance.get_features(current_price))

        if self.analyzer is not None and self.tracker is not None:
            current_price = float(df["close"].iloc[-1])

            if self.tracker.levels:
                distances = [
                    abs(level.price - current_price) / (current_price + 1e-10)
                    for level in self.tracker.levels
                ]
                nearest = self.tracker.levels[int(np.argmin(distances))]
                hold_rate = nearest.hold_rate
                touch_count = nearest.touch_count
                strength = nearest.strength_score
            else:
                hold_rate = touch_count = strength = 0.0

            features.update(
                self.analyzer.get_cross_symbol_features(
                    symbol=self.symbol or "UNKNOWN",
                    hold_rate=hold_rate,
                    touch_count=int(touch_count),
                    strength=strength,
                )
            )
            features.update(
                self.analyzer.get_correlated_pair_feature(
                    symbol=self.symbol or "UNKNOWN",
                    current_price=current_price,
                    all_trackers=self.all_trackers,
                )
            )

        return features

    def compute_series(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute features for every bar. Returns one row per bar."""
        from ml.training.train_reversal_models import compute_technical_features

        tech_feat = compute_technical_features(df)

        frames = [tech_feat]

        if self.tracker is not None:
            frames.append(self.tracker.get_features_series(df))

        if self.significance is not None:
            frames.append(self.significance.get_features_series(df))

        if len(frames) == 1:
            return tech_feat

        return pd.concat(frames, axis=1)

    def build(self, layer_outputs: dict) -> dict:
        """
        Build shared feature dict from pipeline layer inputs.

        Expected keys: symbol, timeframe, timestamp, ohlcv (DataFrame).
        Caches results in FeatureStore and appends Tier-4 method features.
        """
        symbol = str(layer_outputs.get("symbol", "")).upper()
        timeframe = str(layer_outputs.get("timeframe", "5m"))
        timestamp = layer_outputs.get("timestamp")
        ohlcv = layer_outputs.get("ohlcv")

        store = get_feature_store()
        if symbol and timestamp is not None:
            cached = store.get_features(symbol, timeframe, timestamp)
            if cached:
                return cached

        features: dict[str, Any] = dict(layer_outputs.get("base_features") or {})

        if isinstance(ohlcv, pd.DataFrame) and not ohlcv.empty and "close" in ohlcv.columns:
            pipeline = self
            if symbol and (not self.symbol or self.symbol != symbol):
                pipeline = FeaturePipeline.for_symbol(symbol, self.model_dir)
            elif symbol and self.symbol == symbol and self.tracker is None:
                pipeline = FeaturePipeline.for_symbol(symbol, self.model_dir)

            features.update(pipeline.compute(ohlcv))
            self._add_legacy_aliases(features, ohlcv)

            for extract_fn in TIER4_EXTRACTORS:
                features = extract_fn(ohlcv, features)

        if symbol and timestamp is not None:
            store.set_features(symbol, timeframe, timestamp, features)

        return features

    def _technical_features(self, df: pd.DataFrame) -> dict:
        """Compute technical features for the last bar (same as training)."""
        from ml.training.train_reversal_models import compute_technical_features

        tech = compute_technical_features(df)
        if tech.empty:
            return {}
        row = tech.iloc[-1]
        return {
            key: float(value) if pd.notna(value) else 0.0
            for key, value in row.items()
        }

    @staticmethod
    def _add_legacy_aliases(features: dict[str, Any], ohlcv: pd.DataFrame) -> None:
        """Keep legacy keys used by method agents and older tests."""
        close = ohlcv["close"].astype(float)
        features["close"] = float(close.iloc[-1])
        if "macd_line" in features:
            features.setdefault("macd", features["macd_line"])
        if "macd_histogram" in features:
            features.setdefault("macd_hist", features["macd_histogram"])
