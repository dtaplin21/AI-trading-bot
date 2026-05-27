"""Candlestick psychology detection and scoring."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CandlestickPsychology:
    body_to_range_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_location_in_range: float
    rejection_score: float
    indecision_score: float
    exhaustion_score: float
    continuation_score: float
    candle_momentum_score: float
    candle_reversal_probability: float
    pattern_name: str | None = None


class CandlestickPsychologyService:
    """Detects candlestick patterns and psychology metrics."""

    def analyze_bar(self, open_: float, high: float, low: float, close: float) -> CandlestickPsychology:
        rng = high - low or 1e-9
        body = abs(close - open_)
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low

        body_ratio = body / rng
        upper_ratio = upper_wick / rng
        lower_ratio = lower_wick / rng
        close_loc = (close - low) / rng

        indecision = 1.0 - body_ratio if body_ratio < 0.15 else 0.0
        rejection = max(upper_ratio, lower_ratio) * (1 - body_ratio)
        bullish = close > open_
        pattern = self._detect_pattern(body_ratio, upper_ratio, lower_ratio, bullish)

        return CandlestickPsychology(
            body_to_range_ratio=body_ratio,
            upper_wick_ratio=upper_ratio,
            lower_wick_ratio=lower_ratio,
            close_location_in_range=close_loc,
            rejection_score=rejection,
            indecision_score=indecision,
            exhaustion_score=rejection * 0.8 if body_ratio > 0.6 else 0.0,
            continuation_score=body_ratio if body_ratio > 0.5 else 0.0,
            candle_momentum_score=body_ratio * (1 if bullish else -1),
            candle_reversal_probability=rejection,
            pattern_name=pattern,
        )

    def analyze(self, ohlcv: pd.DataFrame) -> CandlestickPsychology:
        row = ohlcv.iloc[-1]
        return self.analyze_bar(row["open"], row["high"], row["low"], row["close"])

    def _detect_pattern(
        self, body_ratio: float, upper: float, lower: float, bullish: bool
    ) -> str | None:
        if body_ratio < 0.1:
            return "doji"
        if lower > 0.6 and body_ratio < 0.35:
            return "hammer" if bullish else "hanging_man"
        if upper > 0.6 and body_ratio < 0.35:
            return "shooting_star" if not bullish else "inverted_hammer"
        if body_ratio > 0.85:
            return "marubozu"
        return None
