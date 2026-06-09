"""Full feature assembly pipeline — shared indicators computed once per bar."""
from __future__ import annotations

from typing import Any

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


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain.iloc[-1] / (loss.iloc[-1] + 1e-9)
    return float(100 - (100 / (1 + rs)))


def _macd(close: pd.Series) -> tuple[float, float, float]:
    if len(close) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd_line = ema12 - ema26
    signal = _ema(macd_line, 9)
    hist = macd_line - signal
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def _atr(ohlcv: pd.DataFrame, period: int = 14) -> float:
    if len(ohlcv) < period + 1:
        return 0.0
    high = ohlcv["high"]
    low = ohlcv["low"]
    close = ohlcv["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


class FeaturePipeline:
    """Compute shared technical features once per bar and cache in FeatureStore."""

    def build(self, layer_outputs: dict) -> dict:
        """
        Build shared indicator dict from pipeline layer inputs.

        Expected keys: symbol, timeframe, timestamp, ohlcv (DataFrame).
        Returns cached features when available for the same bar.
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
            close = ohlcv["close"].astype(float)
            features["rsi_14"] = _rsi(close)
            macd, macd_signal, macd_hist = _macd(close)
            features["macd"] = macd
            features["macd_signal"] = macd_signal
            features["macd_hist"] = macd_hist
            features["atr_14"] = _atr(ohlcv)
            features["close"] = float(close.iloc[-1])
            if len(close) >= 2:
                features["return_1"] = float((close.iloc[-1] - close.iloc[-2]) / (close.iloc[-2] + 1e-9))
            if "volume" in ohlcv.columns:
                vol = ohlcv["volume"].astype(float)
                features["volume"] = float(vol.iloc[-1])
                features["volume_sma_20"] = float(vol.tail(20).mean()) if len(vol) else 0.0
            returns = close.pct_change().dropna()
            if len(returns) >= 5:
                features["volatility_5"] = float(returns.tail(5).std())

            for extract_fn in TIER4_EXTRACTORS:
                features = extract_fn(ohlcv, features)

        if symbol and timestamp is not None:
            store.set_features(symbol, timeframe, timestamp, features)

        return features
