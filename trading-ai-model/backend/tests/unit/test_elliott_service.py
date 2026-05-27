"""Tests for Elliott wave probabilistic constraints."""

from engines.wave.elliott_wave_service import ElliottWaveService
from tests.fixtures.sample_ohlcv import sample_ohlcv


def test_returns_probability_distribution():
    svc = ElliottWaveService()
    result = svc.analyze(sample_ohlcv())
    probs = [
        result.wave_1_prob, result.wave_2_prob, result.wave_3_prob,
        result.wave_4_prob, result.wave_5_prob,
        result.wave_a_prob, result.wave_b_prob, result.wave_c_prob,
    ]
    assert abs(sum(probs) - 1.0) < 0.01


def test_no_signal_without_confidence():
    svc = ElliottWaveService(confidence_threshold=0.99)
    result = svc.analyze(sample_ohlcv())
    assert svc.to_signal_contribution(result) == 0.0 or result.can_influence_signal_rank
