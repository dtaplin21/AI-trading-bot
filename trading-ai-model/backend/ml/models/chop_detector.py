"""
ml/models/chop_detector.py

Detects whether the market is currently in a choppy/ranging state
vs a trending state. Returns P(chop) 0.0-1.0.

Was: hardcoded return 0.0
Now: real choppiness detection using multiple indicators

Connects to:
  - TradingPipelineSupervisor — gates whether to enter trades
  - ProbabilityGate — high chop score suppresses entry signals
  - ReversalPredictor — reversals in choppy markets are less reliable

Why this matters:
  A reversal signal with P=0.75 in a trending market is very different
  from the same signal in a choppy, range-bound market. Chop detection
  prevents the system from churning in sideways conditions.

Indicators used:
  1. Choppiness Index (CHOP) — most direct measure
  2. ADX — trend strength (low ADX = chop)
  3. ATR ratio — volatility contraction signals chop
  4. EMA spread — tight EMAs signal range
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Choppiness Index: measures how choppy/consolidated the market is.

    Range:
      > 61.8 = choppy / consolidating
      < 38.2 = strongly trending
      38.2-61.8 = indeterminate
    """
    atr1 = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_sum = atr1.rolling(period).sum()
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    price_range = high_max - low_min

    chop = 100 * np.log10(atr_sum / (price_range + 1e-10)) / np.log10(period)
    return chop.clip(0, 100)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index — measures trend strength.
    Low ADX (< 20) = no trend = choppy market.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm.abs()] = 0
    minus_dm[minus_dm < plus_dm] = 0

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / (atr + 1e-10)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / (atr + 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()

    return adx


class ChopDetector:
    """
    Detects market choppiness from OHLCV bars.
    Returns P(chop) 0.0-1.0 for each candle.
    """

    def __init__(
        self,
        chop_period: int = 14,
        adx_period: int = 14,
        chop_threshold: float = 61.8,
        trend_threshold: float = 38.2,
        adx_threshold: float = 20.0,
    ):
        self.chop_period = chop_period
        self.adx_period = adx_period
        self.chop_threshold = chop_threshold
        self.trend_threshold = trend_threshold
        self.adx_threshold = adx_threshold

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        """Score every candle. Returns P(chop) aligned to df.index."""
        chop_idx = compute_choppiness_index(df, self.chop_period)
        adx = compute_adx(df, self.adx_period)

        chop_range = self.chop_threshold - self.trend_threshold
        chop_prob = ((chop_idx - self.trend_threshold) / chop_range).clip(0, 1)

        adx_prob = 1.0 - (adx / (self.adx_threshold * 2)).clip(0, 1)

        atr = (df["high"] - df["low"]).rolling(14).mean()
        atr_ratio = atr / (atr.rolling(50).mean() + 1e-10)
        atr_prob = 1.0 - (atr_ratio.clip(0, 1.5) / 1.5)

        ema_fast = df["close"].ewm(span=8).mean()
        ema_slow = df["close"].ewm(span=21).mean()
        ema_spread = (ema_fast - ema_slow).abs() / (df["close"] + 1e-10) * 100
        ema_prob = 1.0 - (ema_spread / 0.5).clip(0, 1)

        combined = 0.40 * chop_prob + 0.30 * adx_prob + 0.15 * atr_prob + 0.15 * ema_prob

        return combined.clip(0, 1).round(4)

    def score(self, df: pd.DataFrame) -> float:
        """Score the most recent candle. Returns P(chop) for current bar."""
        if df is None or df.empty or len(df) < 20:
            return 0.5
        scores = self.score_series(df)
        if scores.empty or scores.isna().all():
            return 0.5
        return float(scores.dropna().iloc[-1])

    def is_choppy(self, df: pd.DataFrame, threshold: float = 0.65) -> bool:
        return self.score(df) >= threshold

    def is_trending(self, df: pd.DataFrame, threshold: float = 0.35) -> bool:
        return self.score(df) <= threshold

    def classify(self, df: pd.DataFrame) -> str:
        score = self.score(df)
        if score >= 0.65:
            return "choppy"
        if score <= 0.35:
            return "trending"
        return "neutral"


_default_detector = ChopDetector()


def detect_chop(df: pd.DataFrame) -> float:
    """Quick P(chop) score for the latest candle."""
    return _default_detector.score(df)


def score_chop_series(df: pd.DataFrame) -> pd.Series:
    """Score every candle in a DataFrame."""
    return _default_detector.score_series(df)
