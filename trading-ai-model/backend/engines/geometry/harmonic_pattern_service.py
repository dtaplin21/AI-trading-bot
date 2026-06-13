"""Harmonic pattern detection with ratio validation constraints."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import get_settings


class HarmonicPatternType(str, Enum):
    GARTLEY = "gartley"
    BUTTERFLY = "butterfly"
    CRAB = "crab"
    DEEP_CRAB = "deep_crab"
    BAT = "bat"
    ABCD = "abcd"
    CYPHER = "cypher"
    SHARK = "shark"


@dataclass
class HarmonicResult:
    pattern_type: Optional[str]
    completion_zone: Optional[float]
    ratio_accuracy: float
    pattern_completion_score: float
    harmonic_reversal_zone: Optional[float]
    harmonic_symmetry_score: float
    distance_to_completion_zone_ticks: Optional[float]
    xab_ratio: Optional[float] = None
    abc_ratio: Optional[float] = None
    bcd_ratio: Optional[float] = None
    production_eligible: bool = False


# Ideal ratios per pattern family (XAB, ABC, BCD, XAD where applicable)
PATTERN_RATIOS: dict[HarmonicPatternType, dict[str, float]] = {
    HarmonicPatternType.GARTLEY: {"xab": 0.618, "abc": 0.382, "bcd": 1.272, "xad": 0.786},
    HarmonicPatternType.BAT: {"xab": 0.382, "abc": 0.382, "bcd": 2.618, "xad": 0.886},
    HarmonicPatternType.BUTTERFLY: {"xab": 0.786, "abc": 0.382, "bcd": 2.618, "xad": 1.272},
}


class HarmonicPatternService:
    """
    Detects harmonic patterns with enforced constraints:
    - Ratio tolerance: 2% to 5% maximum
    - Minimum swing size: ATR-adjusted per symbol
    - Minimum sample: 300+ before production weight
    - Must beat random geometric baseline (validated externally)
    - Must be positive EV after fees and slippage (validated externally)
    """

    MIN_TOLERANCE_PCT = 2.0
    MAX_TOLERANCE_PCT = 5.0

    def __init__(
        self,
        ratio_tolerance_pct: Optional[float] = None,
        min_sample_size: Optional[int] = None,
        atr_multiplier: float = 1.5,
    ):
        settings = get_settings()
        tol = ratio_tolerance_pct if ratio_tolerance_pct is not None else settings.harmonic_ratio_tolerance_pct
        self.ratio_tolerance_pct = max(self.MIN_TOLERANCE_PCT, min(self.MAX_TOLERANCE_PCT, tol))
        self.min_sample_size = min_sample_size or settings.min_pattern_sample_size
        self.atr_multiplier = atr_multiplier
        self._historical_sample_count = 0

    def _ratio_within_tolerance(self, actual: float, ideal: float) -> bool:
        if ideal == 0:
            return False
        pct_diff = abs(actual - ideal) / ideal * 100
        return pct_diff <= self.ratio_tolerance_pct

    def _compute_atr(self, ohlcv: pd.DataFrame, period: int = 14) -> float:
        high, low, close = ohlcv["high"], ohlcv["low"], ohlcv["close"]
        prev_close = close.shift(1)
        tr = pd.Series(
            pd.concat(
                [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
        )
        mean_atr = tr.rolling(period).mean()
        return float(np.asarray(mean_atr)[-1])

    def _swing_size_valid(self, swing_size: float, atr: float) -> bool:
        return swing_size >= atr * self.atr_multiplier

    def detect(
        self,
        swings: list[tuple[float, float]],
        ohlcv: pd.DataFrame,
        historical_sample_size: int = 0,
    ) -> HarmonicResult:
        """Detect harmonic pattern from X-A-B-C-D swing points."""
        self._historical_sample_count = historical_sample_size
        production_eligible = historical_sample_size >= self.min_sample_size

        if len(swings) < 5:
            return HarmonicResult(
                pattern_type=None,
                completion_zone=None,
                ratio_accuracy=0.0,
                pattern_completion_score=0.0,
                harmonic_reversal_zone=None,
                harmonic_symmetry_score=0.0,
                distance_to_completion_zone_ticks=None,
                production_eligible=production_eligible,
            )

        x, a, b, c, d = [s[1] for s in swings[-5:]]
        xa = abs(a - x) or 1e-9
        ab = abs(b - a) or 1e-9
        bc = abs(c - b) or 1e-9
        cd = abs(d - c) or 1e-9

        xab = ab / xa
        abc = bc / ab
        bcd = cd / bc

        atr = self._compute_atr(ohlcv)
        if not self._swing_size_valid(abs(d - x), atr):
            return HarmonicResult(
                pattern_type=None,
                completion_zone=d,
                ratio_accuracy=0.0,
                pattern_completion_score=0.0,
                harmonic_reversal_zone=d,
                harmonic_symmetry_score=0.0,
                distance_to_completion_zone_ticks=None,
                xab_ratio=xab,
                abc_ratio=abc,
                bcd_ratio=bcd,
                production_eligible=production_eligible,
            )

        best_match: Optional[HarmonicPatternType] = None
        best_accuracy = 0.0

        for pattern, ratios in PATTERN_RATIOS.items():
            checks = [
                self._ratio_within_tolerance(xab, ratios["xab"]),
                self._ratio_within_tolerance(abc, ratios["abc"]),
                self._ratio_within_tolerance(bcd, ratios["bcd"]),
            ]
            if all(checks):
                acc = float(
                    1.0
                    - np.mean(
                        [
                            abs(xab - ratios["xab"]) / ratios["xab"],
                            abs(abc - ratios["abc"]) / ratios["abc"],
                            abs(bcd - ratios["bcd"]) / ratios["bcd"],
                        ]
                    )
                )
                if acc > best_accuracy:
                    best_accuracy = acc
                    best_match = pattern

        if best_match is None:
            return HarmonicResult(
                pattern_type=None,
                completion_zone=d,
                ratio_accuracy=0.0,
                pattern_completion_score=0.0,
                harmonic_reversal_zone=d,
                harmonic_symmetry_score=0.0,
                distance_to_completion_zone_ticks=None,
                xab_ratio=xab,
                abc_ratio=abc,
                bcd_ratio=bcd,
                production_eligible=production_eligible,
            )

        direction = "bullish" if d < c else "bearish"
        return HarmonicResult(
            pattern_type=f"{direction}_{best_match.value}",
            completion_zone=d,
            ratio_accuracy=best_accuracy,
            pattern_completion_score=best_accuracy,
            harmonic_reversal_zone=d,
            harmonic_symmetry_score=best_accuracy,
            distance_to_completion_zone_ticks=abs(d - c) / (ohlcv["close"].iloc[-1] * 0.0001 + 1e-9),
            xab_ratio=xab,
            abc_ratio=abc,
            bcd_ratio=bcd,
            production_eligible=production_eligible,
        )
