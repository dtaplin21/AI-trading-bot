"""Tests for number theory service."""

from engines.number_theory.number_theory_service import NumberTheoryService


def test_near_369_level():
    svc = NumberTheoryService()
    assert svc.near_369_level(666, 1000) is True
