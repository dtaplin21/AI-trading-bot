"""Probabilistic Elliott Wave labeling — never hard labels."""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import get_settings


@dataclass
class ElliottWaveDistribution:
    wave_1_prob: float
    wave_2_prob: float
    wave_3_prob: float
    wave_4_prob: float
    wave_5_prob: float
    wave_a_prob: float
    wave_b_prob: float
    wave_c_prob: float
    wave_count_confidence: float
    possible_wave_count: str
    wave_sequence_state: str
    impulse_candidate: bool
    correction_candidate: bool
    wave_3_extension_candidate: bool
    wave_5_exhaustion_candidate: bool
    abc_correction_candidate: bool
    fib_wave_alignment_score: float
    can_influence_signal_rank: bool


class ElliottWaveService:
    """
    Probabilistic wave labeling (NOT hard labels).

    Constraints:
    - Never returns a single hard label
    - Always returns probability distribution across wave states
    - Confidence threshold: 0.60+ required to influence SignalRank
    - Cannot create a trade signal by itself
    """

    WAVE_STATES = ("1", "2", "3", "4", "5", "A", "B", "C")

    def __init__(self, confidence_threshold: Optional[float] = None):
        settings = get_settings()
        self.confidence_threshold = (
            confidence_threshold if confidence_threshold is not None else settings.elliott_confidence_threshold
        )

    def _normalize_probs(self, raw: dict[str, float]) -> dict[str, float]:
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}

    def analyze(self, ohlcv: pd.DataFrame) -> ElliottWaveDistribution:
        """Return probability distribution over wave states."""
        close = ohlcv["close"].values
        returns = np.diff(close) / (close[:-1] + 1e-9)
        momentum = float(np.mean(returns[-5:])) if len(returns) >= 5 else 0.0
        volatility = float(np.std(returns[-20:])) if len(returns) >= 20 else 0.01

        raw = {
            "1": max(0.05, 0.15 + momentum * 10),
            "2": max(0.05, 0.10 - momentum * 5),
            "3": max(0.05, 0.20 + momentum * 15),
            "4": max(0.05, 0.12 - volatility * 5),
            "5": max(0.05, 0.15 + momentum * 8 - volatility * 3),
            "A": max(0.05, 0.10 - momentum * 8),
            "B": max(0.05, 0.08),
            "C": max(0.05, 0.10 - momentum * 10),
        }
        probs = self._normalize_probs(raw)

        max_prob = max(probs.values())
        confidence = max_prob
        top_states = sorted(probs.items(), key=lambda x: -x[1])[:3]
        possible_count = "/".join(f"wave_{s}" for s, _ in top_states)

        impulse = probs["3"] + probs["5"] > probs["A"] + probs["C"]
        correction = probs["A"] + probs["B"] + probs["C"] > 0.35

        return ElliottWaveDistribution(
            wave_1_prob=probs["1"],
            wave_2_prob=probs["2"],
            wave_3_prob=probs["3"],
            wave_4_prob=probs["4"],
            wave_5_prob=probs["5"],
            wave_a_prob=probs["A"],
            wave_b_prob=probs["B"],
            wave_c_prob=probs["C"],
            wave_count_confidence=confidence,
            possible_wave_count=possible_count,
            wave_sequence_state=f"possible_{top_states[0][0]}_completion",
            impulse_candidate=impulse,
            correction_candidate=correction,
            wave_3_extension_candidate=probs["3"] > 0.25,
            wave_5_exhaustion_candidate=probs["5"] > 0.20 and momentum < 0,
            abc_correction_candidate=correction,
            fib_wave_alignment_score=min(1.0, confidence + 0.1),
            can_influence_signal_rank=confidence >= self.confidence_threshold,
        )

    def to_signal_contribution(self, result: ElliottWaveDistribution) -> float:
        """Contribution to SignalRank — zero if below confidence threshold."""
        if not result.can_influence_signal_rank:
            return 0.0
        return result.wave_count_confidence
