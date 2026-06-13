"""
risk/correlation_checker.py

Checks correlation between open positions to prevent overexposure
to the same underlying move.

Was: hardcoded (no real logic)
Now: real correlation matrix from historical returns

Connects to:
  - RiskEngine — called before every trade entry
  - PositionSizer — reduces size when correlation is high
  - PaperTrader — prevents opening highly correlated positions

Why it matters:
  ES + MES + NQ is not three independent positions — they all drop
  together in a market selloff. Without this, the system thinks it
  has three 1% risk trades but actually has ~3% correlated exposure.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Known high-correlation pairs — hardcoded as baseline
# These pairs should never be held simultaneously at full size
KNOWN_CORRELATED = {
    frozenset({"ES", "MES"}): 0.99,  # same underlying
    frozenset({"NQ", "MNQ"}): 0.99,  # same underlying
    frozenset({"ES", "NQ"}): 0.85,  # S&P/Nasdaq
    frozenset({"ES", "MNQ"}): 0.84,
    frozenset({"MES", "NQ"}): 0.84,
    frozenset({"MES", "MNQ"}): 0.83,
    frozenset({"BTCUSD", "ETHUSD"}): 0.88,
    frozenset({"BTCUSD", "SOLUSD"}): 0.82,
    frozenset({"BTCUSD", "BNBUSD"}): 0.79,
    frozenset({"EURUSD", "GBPUSD"}): 0.75,
    frozenset({"EURUSD", "AUDUSD"}): 0.70,
    frozenset({"TSLA", "NVDA"}): 0.72,
    frozenset({"AAPL", "MSFT"}): 0.78,
    frozenset({"GC", "EURUSD"}): 0.65,
}


class CorrelationChecker:
    """
    Checks whether adding a new position would create excessive
    correlation with existing open positions.
    """

    def __init__(
        self,
        max_correlation: float = 0.70,  # above this = reject
        warn_correlation: float = 0.55,  # above this = reduce size
        returns_window: int = 60,  # bars for dynamic correlation
    ):
        self.max_correlation = max_correlation
        self.warn_correlation = warn_correlation
        self.returns_window = returns_window
        self._returns_cache: dict[str, pd.Series] = {}

    def check(
        self,
        new_symbol: str,
        open_positions: list[str],
        returns: Optional[dict[str, pd.Series]] = None,
    ) -> dict:
        """
        Check whether opening a new position is safe given existing positions.

        Args:
            new_symbol:     symbol being considered for entry
            open_positions: list of currently open position symbols
            returns:        optional dict of {symbol: returns_series}
                            for dynamic correlation. Falls back to known pairs.

        Returns:
            dict with keys:
              allowed:     bool — can we open this trade?
              max_corr:    float — highest correlation with any open position
              corr_with:   str — which open position is most correlated
              size_factor: float — suggested size reduction (1.0 = full size)
              reason:      str — human readable explanation
        """
        sym = new_symbol.upper()
        open_syms = [s.upper() for s in open_positions if s.upper() != sym]

        if not open_syms:
            return self._allow(1.0, "no open positions")

        max_corr = 0.0
        corr_with = None

        for open_sym in open_syms:
            corr = self._get_correlation(sym, open_sym, returns)
            if corr > max_corr:
                max_corr = corr
                corr_with = open_sym

        if max_corr >= self.max_correlation:
            return {
                "allowed": False,
                "max_corr": round(max_corr, 3),
                "corr_with": corr_with,
                "size_factor": 0.0,
                "reason": (
                    f"correlation {max_corr:.0%} with {corr_with} exceeds max "
                    f"{self.max_correlation:.0%}"
                ),
            }

        if max_corr >= self.warn_correlation:
            size_factor = 1.0 - (
                (max_corr - self.warn_correlation)
                / (self.max_correlation - self.warn_correlation)
            )
            size_factor = round(max(0.25, size_factor), 2)
            return {
                "allowed": True,
                "max_corr": round(max_corr, 3),
                "corr_with": corr_with,
                "size_factor": size_factor,
                "reason": (
                    f"correlation {max_corr:.0%} with {corr_with} — "
                    f"size reduced to {size_factor:.0%}"
                ),
            }

        return self._allow(1.0, f"max correlation {max_corr:.0%} acceptable")

    def exposure(self, symbols: list[str]) -> float:
        """Max pairwise correlation across a symbol set (legacy API)."""
        if len(symbols) <= 1:
            return 0.0
        syms = [s.upper() for s in symbols]
        max_corr = 0.0
        for i, a in enumerate(syms):
            for b in syms[i + 1 :]:
                max_corr = max(max_corr, self._get_correlation(a, b, None))
        return max_corr

    def _get_correlation(
        self,
        sym_a: str,
        sym_b: str,
        returns: Optional[dict],
    ) -> float:
        """Get correlation between two symbols."""
        a = sym_a.upper()
        b = sym_b.upper()
        pair = frozenset({a, b})

        if returns and a in returns and b in returns:
            try:
                series_a = returns[a].dropna().tail(self.returns_window)
                series_b = returns[b].dropna().tail(self.returns_window)
                if len(series_a) >= 20 and len(series_b) >= 20:
                    aligned = pd.concat([series_a, series_b], axis=1).dropna()
                    if len(aligned) >= 20:
                        corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
                        if not np.isnan(corr):
                            return abs(corr)
            except Exception:
                pass

        return KNOWN_CORRELATED.get(pair, 0.0)

    def _allow(self, size_factor: float, reason: str) -> dict:
        return {
            "allowed": True,
            "max_corr": 0.0,
            "corr_with": None,
            "size_factor": size_factor,
            "reason": reason,
        }

    def portfolio_correlation_matrix(self, symbols: list[str]) -> pd.DataFrame:
        """Build correlation matrix for a list of symbols using known pairs."""
        syms = [s.upper() for s in symbols]
        n = len(syms)
        corr = np.eye(n)
        for i, a in enumerate(syms):
            for j, b in enumerate(syms):
                if i != j:
                    corr[i, j] = KNOWN_CORRELATED.get(frozenset({a, b}), 0.0)
        labels = pd.Index(syms)
        return pd.DataFrame(corr, index=labels, columns=labels)


_checker: CorrelationChecker | None = None


def get_correlation_checker() -> CorrelationChecker:
    """Shared checker — thresholds overridable via env."""
    global _checker
    if _checker is None:
        _checker = CorrelationChecker(
            max_correlation=float(os.getenv("RISK_MAX_CORRELATION", "0.70")),
            warn_correlation=float(os.getenv("RISK_WARN_CORRELATION", "0.55")),
        )
    return _checker
