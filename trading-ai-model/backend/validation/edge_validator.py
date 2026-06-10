"""validation/edge_validator.py"""
from __future__ import annotations

import numpy as np
from scipy import stats


class EdgeValidator:
    """
    Confirms that model predictions have statistically significant edge
    above base rate. Uses binomial test — is win rate meaningfully
    above the base rate or just random noise?
    """

    def __init__(self, min_edge_pct: float = 0.03, min_confidence: float = 0.95):
        self.min_edge_pct = min_edge_pct
        self.min_confidence = min_confidence

    def validate(
        self,
        predictions: list,
        outcomes: list,
        base_rate: float,
        threshold: float = 0.62,
    ) -> dict:
        """
        Test whether high-confidence predictions (>= threshold) have
        significantly higher hit rate than the base rate.
        """
        high_conf = [(p, o) for p, o in zip(predictions, outcomes) if p >= threshold]

        if len(high_conf) < 30:
            return {
                "passed": False,
                "reason": f"too few high-confidence predictions ({len(high_conf)} < 30)",
                "n": len(high_conf),
            }

        n_trades = len(high_conf)
        n_wins = sum(1 for _, o in high_conf if o == 1)
        hit_rate = n_wins / n_trades

        p_value = stats.binomtest(
            n_wins, n_trades, base_rate, alternative="greater"
        ).pvalue

        edge_pct = hit_rate - base_rate
        passed = edge_pct >= self.min_edge_pct and p_value <= (1 - self.min_confidence)

        return {
            "passed": passed,
            "n_trades": n_trades,
            "hit_rate": round(hit_rate, 4),
            "base_rate": round(base_rate, 4),
            "edge_pct": round(edge_pct * 100, 2),
            "p_value": round(float(p_value), 4),
            "confidence": round(1 - float(p_value), 4),
            "reason": (
                "passes"
                if passed
                else (
                    f"edge {edge_pct * 100:.1f}% below min "
                    f"{self.min_edge_pct * 100:.1f}% or p={p_value:.3f} not significant"
                )
            ),
        }
