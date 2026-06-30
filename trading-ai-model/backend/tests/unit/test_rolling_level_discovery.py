"""Unit tests for rolling level discovery (no live DB)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.features.rolling_level_discovery import (
    bars_expected_5m,
    classify_discovery_mode,
    is_outside_envelope,
)


def test_bars_expected_5m():
    assert bars_expected_5m(60) == 60 * 24 * 12


def test_is_outside_envelope_above():
    assert is_outside_envelope(101.0, 100.0, 100.5, buffer_pct=0.15)


def test_is_outside_envelope_inside():
    assert not is_outside_envelope(100.2, 100.0, 100.5, buffer_pct=0.15)


def test_classify_regime_shift():
    mode = classify_discovery_mode(120.0, (100.0, 105.0), buffer_pct=0.15)
    assert mode == "regime_shift"


def test_classify_drift():
    mode = classify_discovery_mode(106.0, (100.0, 105.0), buffer_pct=0.15)
    assert mode == "drift"
