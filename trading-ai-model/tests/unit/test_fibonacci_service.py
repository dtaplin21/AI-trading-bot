"""Tests for fibonacci service."""

from engines.geometry.fibonacci_service import FibonacciService


def test_levels_count():
    svc = FibonacciService()
    levels = svc.levels(5100, 4900)
    assert len(levels) == 10
