"""Assembles full signal payload with all layer outputs."""

from typing import Any

import pandas as pd

from engines.geometry.gann_geometry_service import GannGeometryService
from engines.geometry.harmonic_pattern_service import HarmonicPatternService
from engines.wave.elliott_wave_service import ElliottWaveService
from signal_engine.layer_scores import LayerScores
from signal_engine.signal_rank_service import SignalRankService
from signal_engine.signal_schema import TradingSignal


class SignalBuilder:
    """Orchestrates layer services and produces a TradingSignal."""

    def __init__(self):
        self.harmonic = HarmonicPatternService()
        self.elliott = ElliottWaveService()
        self.gann = GannGeometryService()
        self.rank_service = SignalRankService()

    def build(
        self,
        symbol: str,
        setup: str,
        ohlcv: pd.DataFrame,
        swings: list[tuple[float, float]],
        layer_scores: LayerScores,
        extra: dict[str, Any] | None = None,
        risk_approved: bool = False,
        historical_sample_size: int = 0,
    ) -> TradingSignal:
        harmonic = self.harmonic.detect(swings, ohlcv, historical_sample_size)
        elliott = self.elliott.analyze(ohlcv)
        gann = self.gann.analyze(ohlcv, historical_sample_size)

        if elliott.can_influence_signal_rank:
            layer_scores.elliott = elliott.to_signal_contribution(elliott)
        if harmonic.production_eligible and harmonic.pattern_completion_score > 0:
            layer_scores.harmonic = harmonic.pattern_completion_score
        layer_scores.gann_modifier = self.gann.get_signal_rank_modifier(gann)

        payload: dict[str, Any] = {
            "harmonic_pattern": harmonic.pattern_type,
            "xab_ratio": harmonic.xab_ratio,
            "abc_ratio": harmonic.abc_ratio,
            "bcd_ratio": harmonic.bcd_ratio,
            "harmonic_completion_score": harmonic.pattern_completion_score,
            "elliott_state": elliott.wave_sequence_state,
            "wave_3_probability": elliott.wave_3_prob,
            "wave_5_probability": elliott.wave_5_prob,
            "abc_correction_probability": elliott.wave_a_prob + elliott.wave_b_prob + elliott.wave_c_prob,
            "gann_confluence": gann.gann_confluence_score > 0.5,
            "gann_angle_distance": gann.gann_angle_distance,
            "historical_sample_size": historical_sample_size,
        }
        if extra:
            payload.update(extra)

        return self.rank_service.build_signal(
            symbol=symbol,
            setup=setup,
            scores=layer_scores,
            payload=payload,
            risk_approved=risk_approved,
        )
