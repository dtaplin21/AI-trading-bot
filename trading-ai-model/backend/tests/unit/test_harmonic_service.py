"""Tests for harmonic pattern service constraints."""

import pytest

from engines.geometry.harmonic_pattern_service import HarmonicPatternService
from tests.fixtures.sample_ohlcv import sample_ohlcv, sample_swings


def test_tolerance_clamped_to_2_5_pct():
    svc = HarmonicPatternService(ratio_tolerance_pct=10.0)
    assert svc.ratio_tolerance_pct == 5.0
    svc2 = HarmonicPatternService(ratio_tolerance_pct=1.0)
    assert svc2.ratio_tolerance_pct == 2.0


def test_production_eligible_requires_300_samples():
    svc = HarmonicPatternService()
    result = svc.detect(sample_swings(), sample_ohlcv(), historical_sample_size=100)
    assert result.production_eligible is False
    result2 = svc.detect(sample_swings(), sample_ohlcv(), historical_sample_size=300)
    assert result2.production_eligible is True
