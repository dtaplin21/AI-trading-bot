"""Tests for CorrelationChecker."""

from __future__ import annotations

from risk.correlation_checker import CorrelationChecker, KNOWN_CORRELATED


def test_no_open_positions_allows_full_size():
    checker = CorrelationChecker()
    result = checker.check("MES", [])
    assert result["allowed"] is True
    assert result["size_factor"] == 1.0


def test_rejects_highly_correlated_pair():
    checker = CorrelationChecker(max_correlation=0.70)
    result = checker.check("MES", ["NQ"])
    assert result["allowed"] is False
    assert result["max_corr"] >= 0.70
    assert result["corr_with"] == "NQ"


def test_warn_zone_reduces_size():
    checker = CorrelationChecker(max_correlation=0.80, warn_correlation=0.55)
    result = checker.check("TSLA", ["NVDA"])
    assert result["allowed"] is True
    assert 0.25 <= result["size_factor"] < 1.0


def test_known_pair_lookup():
    checker = CorrelationChecker()
    corr = checker._get_correlation("ES", "MES", None)
    assert corr == KNOWN_CORRELATED[frozenset({"ES", "MES"})]


def test_portfolio_matrix_shape():
    checker = CorrelationChecker()
    matrix = checker.portfolio_correlation_matrix(["MES", "NQ", "EURUSD"])
    assert matrix.shape == (3, 3)
    assert matrix.loc["MES", "MES"] == 1.0
