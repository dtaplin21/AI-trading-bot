"""Tests for candlestick psychology service."""

from engines.price_action.candlestick_psychology_service import CandlestickPsychologyService
from tests.fixtures.sample_ohlcv import sample_ohlcv


def test_analyze_returns_metrics():
    svc = CandlestickPsychologyService()
    result = svc.analyze(sample_ohlcv())
    assert 0 <= result.body_to_range_ratio <= 1
    assert 0 <= result.close_location_in_range <= 1


def test_detects_doji():
    svc = CandlestickPsychologyService()
    result = svc.analyze_bar(100, 101, 99, 100.05)
    assert result.pattern_name == "doji"
