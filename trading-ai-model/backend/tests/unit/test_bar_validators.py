"""Tests for bar_validators."""

from pipeline.bar_validators import is_valid_bar_close


def test_is_valid_bar_close_rejects_bad_values():
    assert is_valid_bar_close(100.5)
    assert not is_valid_bar_close(0.0)
    assert not is_valid_bar_close(None)
