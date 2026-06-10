"""
ml/models/continuation_predictor.py

Predicts P(continuation) — probability that the current trend
continues for at least N more bars without reversing.

Was: hardcoded return 0.5
Now: real continuation scoring using trend strength indicators

Connects to:
  - TradingPipelineSupervisor — used alongside reversal_predictor
  - ChopDetector — low chop + high continuation = strong trend
  - ConfluenceAgent — one of the method agent signals
  - ProbabilityGate — high continuation suppresses reversal entry

Note:
  continuation + reversal probabilities don't need to sum to 1.
  A bar can have low P(reversal) AND low P(continuation) —
  that's the ambiguous/choppy zone where we don't trade.
"""
from __future__ import annotations

import logging
from typing import cast

import pandas as pd

logger = logging.getLogger(__name__)


class ContinuationPredictor:
    """
    Scores P(continuation) — how likely is the current trend to persist.

    Factors:
      1. Trend alignment — price position vs multiple EMAs
      2. Momentum — RSI above/below 50, MACD direction
      3. Volume confirmation — trending bars have higher volume
      4. Candle structure — bodies larger than wicks = trend bars
      5. ATR expansion — trends expand ATR
    """

    def __init__(
        self,
        forward_bars: int = 12,
        min_move_pct: float = 0.10,
    ):
        self.forward_bars = forward_bars
        self.min_move_pct = min_move_pct

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        """Score P(continuation) for every bar. Returns 0.0-1.0 probabilities."""
        close = df["close"]

        ema8 = close.ewm(span=8, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        bull_align = (
            (close > ema8) & (ema8 > ema21) & (ema21 > ema50)
        ).astype(float)
        bear_align = (
            (close < ema8) & (ema8 < ema21) & (ema21 < ema50)
        ).astype(float)
        trend_align_score = bull_align + bear_align

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = pd.Series(100 - (100 / (1 + rs)), index=close.index)

        bull_trend = (close > ema21).astype(float)
        bear_trend = (close < ema21).astype(float)
        bull_rsi = (rsi - 50).div(50).clip(lower=0, upper=1)
        bear_rsi = (50 - rsi).div(50).clip(lower=0, upper=1)
        rsi_score = bull_trend * bull_rsi + bear_trend * bear_rsi

        if "volume" in df.columns:
            vol_ma = df["volume"].rolling(20).mean()
            vol_ratio = (df["volume"] / (vol_ma + 1e-10)).clip(0, 3) / 3
            on_trend = (close.diff() > 0).eq(close > ema21).astype(float)
            vol_score = vol_ratio * on_trend
        else:
            vol_score = pd.Series(0.5, index=df.index)

        body_size = (df["close"] - df["open"]).abs()
        total_size = df["high"] - df["low"] + 1e-10
        body_ratio = (body_size / total_size).clip(0, 1)
        candle_score = body_ratio

        tr = pd.Series(
            pd.concat(
                [
                    df["high"] - df["low"],
                    (df["high"] - close.shift()).abs(),
                    (df["low"] - close.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1),
            index=close.index,
        )
        atr = cast(pd.Series, tr.rolling(14).mean())
        atr_ratio = (atr / (atr.rolling(50).mean() + 1e-10)).clip(0, 2) / 2

        combined = (
            0.30 * trend_align_score
            + 0.25 * rsi_score
            + 0.20 * vol_score
            + 0.15 * candle_score
            + 0.10 * atr_ratio
        )

        return combined.clip(0, 1).round(4)

    def score(self, df: pd.DataFrame) -> float:
        """Score the most recent candle."""
        if df is None or df.empty or len(df) < 20:
            return 0.5
        scores = self.score_series(df)
        if scores.empty or scores.isna().all():
            return 0.5
        return float(scores.dropna().iloc[-1])

    def score_with_direction(self, df: pd.DataFrame) -> tuple[float, str]:
        """Returns (P(continuation), direction) where direction is up/down/none."""
        score = self.score(df)
        if score < 0.4:
            return score, "none"

        close = df["close"]
        ema21 = close.ewm(span=21, adjust=False).mean()

        if float(close.iloc[-1]) > float(ema21.iloc[-1]):
            return score, "up"
        return score, "down"


_default_predictor = ContinuationPredictor()


def predict_continuation(df: pd.DataFrame) -> float:
    """Quick P(continuation) for the latest candle."""
    return _default_predictor.score(df)
