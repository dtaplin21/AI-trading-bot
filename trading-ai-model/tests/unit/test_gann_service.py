"""Tests for Gann geometry research-only constraints."""

from engines.geometry.gann_geometry_service import GannGeometryService
from tests.fixtures.sample_ohlcv import sample_ohlcv


def test_research_only_by_default():
    svc = GannGeometryService()
    result = svc.analyze(sample_ohlcv())
    assert result.research_only is True
    assert svc.get_signal_rank_modifier(result) == 0.0


def test_modifier_requires_sample_and_baseline():
    svc = GannGeometryService(research_only=False)
    result = svc.analyze(sample_ohlcv(), historical_sample_size=300, baseline_beats_random=True)
    assert result.can_influence_signal_rank is True
